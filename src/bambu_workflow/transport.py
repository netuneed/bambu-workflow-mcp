"""Bambu LAN transport, implemented from the on-wire protocol.

No command in this module treats an MQTT PUBACK as evidence of printer motion.
The workflow owns durable dispatch intent, idempotency and job reconciliation.
"""
from __future__ import annotations

import contextlib
import ftplib
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import socket
import ssl
import struct
import tempfile
import threading
import time
from typing import Any, Callable
import uuid
import zipfile


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str, uncertain: bool = False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


CAPABILITIES = {
    "a1mini": {
        "telemetry": True, "files": True, "upload": True, "start": True,
        "pause": True, "resume": True, "cancel": True, "camera": True,
        "temperature": True, "fan": True, "speed": True,
        "ams_lite_inventory": True, "ams_drying": False,
        "chamber_heater": False, "auxiliary_fan": False,
        "automatic_part_removal": False,
        "hardware_status": "launch_and_first_layer_verified", "protocol": "lan_mqtt_ftps",
        "tested_firmware": "01.08.01.00",
        "hardware_verified": ["telemetry", "files", "upload_sha256", "camera", "start", "printing_identity"],
        "hardware_not_yet_verified": ["pause", "resume", "cancel", "temperature", "fan", "speed", "completed_job"],
    },
}
_MODEL_ALIASES = {"a1m": "a1mini", "a1 mini": "a1mini", "a1_mini": "a1mini"}
_SERIAL_MODELS = {"030": "a1mini", "039": "a1", "01S": "p1p", "01P": "p1s", "00M": "x1c", "00W": "x1", "03W": "x1e"}
_IDLE = {"IDLE", "FINISH", "FAILED"}
_ACTIVE = {"RUNNING", "PREPARE", "SLICING", "PAUSE"}
_STAGES = {0: "printing", 1: "bed_leveling", 2: "bed_preheating", 3: "vibration_calibration",
           4: "changing_filament", 5: "gcode_pause", 6: "filament_runout_pause", 7: "nozzle_heating",
           8: "extrusion_calibration", 13: "homing", 14: "nozzle_cleaning", 16: "user_pause",
           54: "waiting_for_bed_temperature", 255: "idle"}


def normalize_model(value: Any) -> str | None:
    if not value:
        return None
    model = str(value).lower().strip()
    return _MODEL_ALIASES.get(model, model)


def _remote_path(value: str, *, directory: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(c in value for c in "\r\n\0"):
        raise TransportError("INVALID_REMOTE_PATH", "Use a relative SD-card path without control characters.")
    pieces = value.split("/")
    if value.startswith("/") or any(p in {".", "..", ""} for p in pieces):
        raise TransportError("INVALID_REMOTE_PATH", "Absolute and traversing remote paths are refused.")
    if not directory and (len(pieces) != 2 or pieces[0] != "cache" or not re.fullmatch(r"[A-Za-z0-9_.-]{1,150}", pieces[1])):
        raise TransportError("INVALID_REMOTE_PATH", "Managed files must have a simple unique filename under cache/.")
    return str(PurePosixPath(value))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _ImplicitFTPS(ftplib.FTP_TLS):
    """Implicit TLS on 990 and reuse of the control TLS session for data."""

    def connect(self, host: str = "", port: int = 990, timeout: float = 15, source_address=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.source_address = source_address
        plain = socket.create_connection((host, port), timeout, source_address)
        try:
            self.sock = self.context.wrap_socket(plain, server_hostname=host)
            self.af = self.sock.family
            self.file = self.sock.makefile("r", encoding=self.encoding)
            self.welcome = self.getresp()
            return self.welcome
        except BaseException:
            plain.close()
            raise

    def ntransfercmd(self, cmd: str, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            try:
                conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
            except BaseException:
                conn.close()
                raise
        return conn, size

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        """Bambu's FTP server can omit TLS close_notify on the upload data socket.

        Closing the data socket ends STOR without the unbounded peer-shutdown
        exchange in FTP_TLS.storbinary(). The control reply and a full RETR hash
        remain required before the workflow calls an upload verified.
        """
        self.voidcmd("TYPE I")
        connection = self.transfercmd(cmd, rest)
        try:
            while True:
                block = fp.read(blocksize)
                if not block:
                    break
                connection.sendall(block)
                if callback:
                    callback(block)
        finally:
            connection.close()
        return self.voidresp()


class BambuLAN:
    def __init__(self, printer: dict, access_code: str):
        self.printer = dict(printer)
        self.host = str(printer.get("host", "")).strip()
        self.serial = str(printer.get("serial", "")).strip()
        self.model = normalize_model(printer.get("model"))
        self._code = access_code
        self.timeout = float(printer.get("timeout", 15))
        if not self.host or not re.fullmatch(r"[A-Za-z0-9]{8,32}", self.serial):
            raise TransportError("NOT_CONFIGURED", "Configure the printer host and serial number.")
        if not isinstance(access_code, str) or not access_code or len(access_code) > 32 or not access_code.isascii() or any(c in access_code for c in "\r\n\0"):
            raise TransportError("MISSING_SECRET", "The configured printer access-code secret is missing or invalid.")
        if not math.isfinite(self.timeout) or not 1 <= self.timeout <= 120:
            raise TransportError("INVALID_TIMEOUT", "Printer timeout must be between 1 and 120 seconds.")
        # Trust decisions are configuration, never silently inferred from failed TLS.
        self.tls_verify = printer.get("tls_verify", True)
        if type(self.tls_verify) is not bool:
            raise TransportError("INVALID_TLS_CONFIG", "tls_verify must be a JSON boolean.")
        self.fingerprint = re.sub(r"[^a-fA-F0-9]", "", str(printer.get("tls_fingerprint", ""))).lower()
        if self.fingerprint and not re.fullmatch(r"[a-f0-9]{64}", self.fingerprint):
            raise TransportError("INVALID_FINGERPRINT", "tls_fingerprint must be a SHA256 certificate fingerprint.")

    def capabilities(self) -> dict:
        return dict(CAPABILITIES.get(self.model, {"telemetry": True, "hardware_status": "unverified", "start": False,
                                                   "reason": "This model has no enabled write adapter."}))

    def _write_supported(self, capability: str):
        if not self.capabilities().get(capability):
            raise TransportError("UNSUPPORTED_OPERATION", f"The {capability} operation is not enabled for model {self.model}.")

    def _context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        if self.tls_verify is False:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if self.printer.get("tls_ca_file"):
            context.load_verify_locations(cafile=self.printer["tls_ca_file"])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def _pin(self, sock: ssl.SSLSocket):
        if self.fingerprint and hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest() != self.fingerprint:
            raise TransportError("TLS_IDENTITY", "Printer TLS certificate differs from the configured fingerprint.")

    def _safe_error(self, stage: str, error: BaseException, *, uncertain: bool = False) -> TransportError:
        if isinstance(error, TransportError):
            return error
        # Do not forward library exception text: URLs, credentials or packets can be included.
        reason = type(error).__name__
        return TransportError(stage, f"{stage}: {reason}. Check LAN reachability, access code, LAN/Developer Mode and SD card.", uncertain)

    def _exchange(self, requests: list[dict], ready: Callable[[list[dict]], bool], timeout: float, *, mutation: bool = False) -> list[dict]:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise TransportError("DEPENDENCY", "Install paho-mqtt to use the LAN transport.") from exc
        deadline = time.monotonic() + timeout
        packets: list[dict] = []
        failure: list[BaseException] = []
        done = threading.Event()
        subscribed = threading.Event()
        dispatched = threading.Event()
        tls_context = self._context()
        connection_stage = {"value": "tcp"}

        class BoundedClient(mqtt.Client):
            # paho 2.x otherwise uses MQTT keepalive as the TLS-handshake timeout.
            # Both phases share this operation's deadline, including a silent TLS peer.
            def _create_socket_connection(inner):
                remaining = max(0.01, deadline - time.monotonic())
                stream = socket.create_connection((self.host, 8883), timeout=min(self.timeout, remaining))
                connection_stage["value"] = "tls"
                return stream

            def _ssl_wrap_socket(inner, tcp_sock):
                connection_stage["value"] = "tls"
                remaining = max(0.01, deadline - time.monotonic())
                tcp_sock.settimeout(min(self.timeout, remaining))
                stream = tls_context.wrap_socket(tcp_sock, server_hostname=self.host)
                connection_stage["value"] = "mqtt"
                return stream

        client = BoundedClient(mqtt.CallbackAPIVersion.VERSION2, client_id="bwf_" + uuid.uuid4().hex[:18], clean_session=True)
        client.username_pw_set("bblp", self._code)
        client.tls_set_context(tls_context)
        client.connect_timeout = min(self.timeout, timeout)
        if self.tls_verify is False:
            client.tls_insecure_set(True)
        client.reconnect_delay_set(120, 120)
        client.suppress_exceptions = True

        def on_connect(cli, userdata, flags, reason_code, properties):
            if reason_code != 0:
                failure.append(TransportError("MQTT_AUTH", "Printer MQTT connection or authentication was rejected."))
                done.set()
                return
            try:
                self._pin(cli.socket())
                cli.subscribe(f"device/{self.serial}/report", qos=0)
            except BaseException as exc:
                failure.append(exc)
                done.set()

        def on_subscribe(cli, userdata, mid, reason_codes, properties):
            if any(code.is_failure for code in reason_codes):
                failure.append(TransportError("MQTT_SUBSCRIBE", "Printer refused the report-topic subscription."))
                done.set()
            else:
                subscribed.set()

        def on_message(cli, userdata, message):
            if message.retain or not dispatched.is_set():
                return
            try:
                value = json.loads(message.payload)
                if isinstance(value, dict):
                    packets.append({"received_at": time.time(), "payload": value})
                    if ready(packets):
                        done.set()
            except (ValueError, UnicodeError):
                pass

        client.on_connect, client.on_subscribe, client.on_message = on_connect, on_subscribe, on_message
        try:
            try:
                client.connect(self.host, 8883, keepalive=30)
            except (TimeoutError, socket.timeout):
                raise TransportError("MQTT_TCP_TIMEOUT", "The printer did not accept a new MQTT/TLS connection on port 8883. Existing monitoring clients may exhaust connection slots (unconfirmed). After the current print, close unused monitoring connections and retry. Authentication and command support have not yet been tested.") from None
            except ConnectionResetError:
                code = "MQTT_TLS_RESET" if connection_stage["value"] == "tls" else "MQTT_CONNECT_RESET"
                raise TransportError(code, "The printer reset the new connection before the MQTT session was established. Existing monitoring clients may exhaust connection slots (unconfirmed); preserve active sessions and close only an explicitly unused monitor. This failure does not establish an access-code or command-verification problem.") from None
            client.loop_start()
            if not subscribed.wait(max(0, deadline - time.monotonic())):
                if failure:
                    raise failure[0]
                raise TransportError("MQTT_CONNECT_TIMEOUT", "No MQTT report subscription was established before the timeout.")
            dispatched.set()
            for request in requests:
                # Some A1 mini firmware sends reports for read requests but does not
                # acknowledge QoS-1 publications. Read requests have no physical
                # effect, so use QoS 0 and wait for fresh reports, not a PUBACK.
                qos = 1 if mutation else 0
                info = client.publish(f"device/{self.serial}/request", json.dumps(request, separators=(",", ":")), qos=qos, retain=False)
                info.wait_for_publish(timeout=max(0.01, deadline - time.monotonic()))
                if not info.is_published():
                    # A firmware command reply can arrive even when its broker's
                    # QoS-1 PUBACK does not. Preserve that exact reply without
                    # publishing again. Its meaning is decided by the caller;
                    # it is never itself evidence that printing has begun.
                    if mutation and ready(packets):
                        return packets
                    raise TransportError("MQTT_PUBLISH_UNCERTAIN", "MQTT publish confirmation was lost. Reconcile printer state before any retry.", mutation)
            done.wait(max(0, deadline - time.monotonic()))
            if failure:
                raise failure[0]
            return packets
        except BaseException as exc:
            raise self._safe_error("MQTT_ERROR", exc, uncertain=mutation and dispatched.is_set()) from None
        finally:
            with contextlib.suppress(Exception):
                client.disconnect()
            with contextlib.suppress(Exception):
                client.loop_stop()

    @staticmethod
    def _status_parts(packets: list[dict]) -> tuple[dict, dict, float]:
        status, info = {}, {}
        received_at = 0.0
        for packet in packets:
            payload = packet["payload"]
            if isinstance(payload.get("print"), dict) and payload["print"].get("command") == "push_status":
                status.update(payload["print"])
                received_at = packet["received_at"]
            if isinstance(payload.get("info"), dict) and payload["info"].get("command") == "get_version":
                info.update(payload["info"])
        return status, info, received_at

    def snapshot(self, timeout: float = 12) -> dict:
        timeout = min(float(timeout), 120)
        seq = str(int(time.time() * 1000) % 2_147_483_647)
        def ready(packets):
            status, info, _ = self._status_parts(packets)
            return "gcode_state" in status and "nozzle_diameter" in status and bool(info)
        packets = self._exchange([
            {"pushing": {"command": "pushall", "sequence_id": seq, "version": 1, "push_target": 1}},
            {"info": {"command": "get_version", "sequence_id": str(int(seq) + 1)}},
        ], ready, timeout)
        raw, info, received_at = self._status_parts(packets)
        if not raw or "gcode_state" not in raw:
            raise TransportError("TELEMETRY_TIMEOUT", "No fresh printer status was received. A TCP connection alone does not establish telemetry.")
        modules = info.get("module", [])
        ota = next((m for m in modules if m.get("name") == "ota"), {})
        live_serial = str(ota.get("sn", ""))
        reported_model = normalize_model(raw.get("model") or info.get("model"))
        if not reported_model and live_serial:
            reported_model = _SERIAL_MODELS.get(live_serial[:3])
        if live_serial and live_serial != self.serial:
            raise TransportError("PRINTER_IDENTITY", "The printer's live serial number differs from the configured device.")
        ams = raw.get("ams", {})
        external = raw.get("vt_tray", {})
        material = {"external": external, "ams": ams, "source": "printer_metadata",
                    "physical_spool_verified": False, "remaining_mass_verified": False}
        stage = raw.get("stage", {}).get("_id", raw.get("stg_cur"))
        try:
            stage = int(stage) if stage is not None else None
        except (ValueError, TypeError):
            stage = None
        return {
            "received_at": received_at, "fresh": time.time() - received_at <= 15,
            "model": reported_model, "configured_model": self.model,
            "identity_verified": bool(live_serial == self.serial and reported_model),
            "identity_source": "live_ota_serial" if live_serial else "live_model_field" if reported_model else "unavailable",
            "firmware": ota.get("sw_ver"), "modules": modules,
            "gcode_state": raw.get("gcode_state"),
            "nozzle_diameter": raw.get("nozzle_diameter"), "nozzle_type": raw.get("nozzle_type"),
            "hms": raw.get("hms", []), "print_error": raw.get("print_error", 0),
            "gcode_file": raw.get("gcode_file"), "subtask_name": raw.get("subtask_name"),
            "layer_num": raw.get("layer_num"), "mc_print_stage": raw.get("mc_print_stage"),
            "stage_id": stage, "stage": _STAGES.get(stage, "unknown"),
            "stage_source": "stage._id" if "stage" in raw else "stg_cur" if "stg_cur" in raw else None,
            "progress": raw.get("mc_percent"), "remaining_minutes": raw.get("mc_remaining_time"),
            "temperatures": {"nozzle": raw.get("nozzle_temper"), "nozzle_target": raw.get("nozzle_target_temper"),
                             "bed": raw.get("bed_temper"), "bed_target": raw.get("bed_target_temper")},
            "material": material, "raw": raw,
        }

    def _ready(self, snapshot: dict, *, idle: bool = True):
        if not snapshot.get("received_at") or time.time() - snapshot["received_at"] > 15:
            raise TransportError("STALE_TELEMETRY", "Fresh printer telemetry is required before a hardware command.")
        if snapshot.get("model") != self.model or not snapshot.get("identity_verified"):
            raise TransportError("PRINTER_IDENTITY", "Live model and serial must match the configured printer before hardware control.")
        if idle and snapshot.get("gcode_state") not in _IDLE:
            raise TransportError("PRINTER_BUSY", "The printer is occupied. The current print must finish or be explicitly canceled.")
        if snapshot.get("hms") or snapshot.get("print_error"):
            raise TransportError("PRINTER_ERROR", "The printer reports HMS or print errors. Resolve the physical cause before continuing.")
        if self.printer.get("nozzle_diameter") is not None:
            try:
                matches = math.isclose(float(snapshot.get("nozzle_diameter")), float(self.printer["nozzle_diameter"]), abs_tol=0.001)
            except (TypeError, ValueError):
                matches = False
            if not matches:
                raise TransportError("NOZZLE_MISMATCH", "The live nozzle diameter differs from the configured nozzle.")
        if self.printer.get("nozzle_type") and snapshot.get("nozzle_type") != self.printer["nozzle_type"]:
            raise TransportError("NOZZLE_MISMATCH", "The live nozzle material differs from the configured nozzle.")

    @contextlib.contextmanager
    def _ftp(self):
        ftp = _ImplicitFTPS(context=self._context(), timeout=self.timeout)
        try:
            ftp.connect(self.host, 990, self.timeout)
            self._pin(ftp.sock)
            ftp.login("bblp", self._code)
            ftp.prot_p()
            # Use the authenticated control host even if a PASV response advertises another address.
            ftp.trust_server_pasv_ipv4_address = False
            yield ftp
        except BaseException as exc:
            raise self._safe_error("FTPS_ERROR", exc) from None
        finally:
            with contextlib.suppress(Exception):
                ftp.close()

    def list_files(self, path: str = "cache") -> list[dict]:
        path = _remote_path(path, directory=True)
        if path not in {"cache", "timelapse", "log", "logs"}:
            raise TransportError("INVALID_REMOTE_PATH", "List only cache/, timelapse/ or logs/ directories.")
        with self._ftp() as ftp:
            try:
                return [{"name": name, "path": f"{path}/{name}", **facts} for name, facts in ftp.mlsd(path) if name not in {".", ".."}]
            except ftplib.error_perm as exc:
                if not str(exc).startswith(("500", "502", "504")):
                    raise
                rows: list[str] = []
                ftp.retrlines("LIST " + path, rows.append)
                # Keep the original row as evidence rather than guessing device-specific date formats.
                result = []
                for row in rows:
                    parts = row.split(maxsplit=8)
                    if len(parts) == 9:
                        result.append({"name": parts[8], "path": f"{path}/{parts[8]}", "size": parts[4], "raw_listing": row})
                return result

    @staticmethod
    def _remote_hash(ftp: ftplib.FTP_TLS, remote: str) -> dict:
        sha256, md5, total = hashlib.sha256(), hashlib.md5(), 0
        def consume(block):
            nonlocal total
            total += len(block)
            if total > 512 * 1024 * 1024:
                raise TransportError("FILE_TOO_LARGE", "Remote artifact exceeds the 512 MiB verification bound.")
            sha256.update(block)
            md5.update(block)
        ftp.retrbinary("RETR " + remote, consume, blocksize=256 * 1024)
        return {"sha256": sha256.hexdigest(), "md5": md5.hexdigest(), "bytes": total}

    @staticmethod
    def _remote_print_evidence(ftp: ftplib.FTP_TLS, remote: str, plate: int) -> dict:
        """Hash the retrieved container and the exact selected nested G-code.

        The project_file protocol MD5 covers plate_N.gcode, not the 3MF ZIP.
        Nothing from the untrusted archive is extracted into the filesystem.
        """
        digest, size = hashlib.sha256(), 0
        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as spool:
            def consume(block):
                nonlocal size
                size += len(block)
                if size > 512 * 1024 * 1024:
                    raise TransportError("FILE_TOO_LARGE", "Remote artifact exceeds the 512 MiB verification bound.")
                digest.update(block)
                spool.write(block)
            ftp.retrbinary("RETR " + remote, consume, blocksize=256 * 1024)
            spool.seek(0)
            try:
                with zipfile.ZipFile(spool) as archive:
                    entries = archive.infolist()
                    names = [entry.filename for entry in entries]
                    if len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > 1024 * 1024 * 1024:
                        raise TransportError("INVALID_PRINT_FILE", "The remote archive has duplicate entries or exceeds the expanded-size bound.")
                    selected = f"Metadata/plate_{plate}.gcode"
                    if selected not in names:
                        raise TransportError("INVALID_PLATE", "The selected internal G-code plate is absent from the uploaded artifact.")
                    info = archive.getinfo(selected)
                    if info.file_size > 256 * 1024 * 1024 or info.file_size == 0:
                        raise TransportError("INVALID_PRINT_FILE", "Selected G-code is empty or exceeds 256 MiB.")
                    md5, legacy_selects_filament, head = hashlib.md5(), False, b""
                    with archive.open(selected) as stream:
                        for line in stream:
                            md5.update(line)
                            if len(head) < 32768:
                                head += line[:32768-len(head)]
                            code = line.split(b";", 1)[0].strip()
                            # gcode_file cannot apply the project's filament-to-physical-tray map.
                            if re.match(rb"M62[01]\s+S(?:[0-9]|1[0-5])(?:A|\s|$)", code) or re.match(rb"T(?:[0-9]|1[0-5])(?:\s|$)", code):
                                legacy_selects_filament = True
                    colours = re.search(rb";\s*filament_colour\s*=\s*([^\r\n]+)", head)
                    filament_count = len([value for value in colours[1].split(b";") if value.strip()]) if colours else 1
                    return {"sha256": digest.hexdigest(), "gcode_md5": md5.hexdigest(), "bytes": size,
                            "plate_path": selected, "plate_paths": [name for name in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", name)],
                            "legacy_selects_filament": legacy_selects_filament, "filament_count": filament_count}
            except zipfile.BadZipFile:
                raise TransportError("INVALID_PRINT_FILE", "The remote artifact is not a readable sliced 3MF ZIP.") from None

    def upload(self, local: Path, remote: str, expected_sha256: str) -> dict:
        self._write_supported("upload")
        remote = _remote_path(remote)
        local = Path(local)
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256) or not local.is_file():
            raise TransportError("INVALID_ARTIFACT", "Upload requires a local artifact and its expected SHA256.")
        if local.stat().st_size > 512 * 1024 * 1024 or _hash_file(local) != expected_sha256:
            raise TransportError("ARTIFACT_CHANGED", "The local artifact changed after validation or exceeds 512 MiB.")
        attempt = {"stor_started": False}
        try:
            return self._upload_once(local, remote, expected_sha256, attempt)
        except Exception as exc:
            error = self._safe_error("FTPS_ERROR", exc)
            if not attempt["stor_started"] or error.code in {"ARTIFACT_CHANGED", "TRANSFER_CORRUPT"}:
                raise error from None
            # Lost control completion is not evidence that a complete data transfer failed.
            # Reconcile once using a NEW read-only FTP connection. Never repeat STOR here.
            try:
                with self._ftp() as ftp:
                    hashes = self._remote_hash(ftp, remote)
            except Exception:
                raise TransportError("TRANSFER_UNCERTAIN", "Upload completion was lost and one independent read-only verification failed. Do not start; inspect the remote file or retry with a new filename.") from None
            if hashes["sha256"] != expected_sha256:
                raise TransportError("TRANSFER_INCOMPLETE", "The remote file after the interrupted upload has a different SHA256. Do not start or overwrite it; retry under a new unique filename.") from None
            return {"state": "transferred", "remote": remote, "verified_by": "full_retrieve_sha256",
                    "control_completion_uncertain": True, "recovered_after": error.code,
                    "stor_attempts": 1, **hashes}

    def _upload_once(self, local: Path, remote: str, expected_sha256: str, attempt: dict) -> dict:
        with self._ftp() as ftp:
            # Never replace an unrelated existing file. A retry may reuse the exact verified content.
            try:
                ftp.voidcmd("TYPE I")
                existing = ftp.size(remote)
            except ftplib.error_perm as exc:
                if not str(exc).startswith("550"):
                    raise
                existing = None
            if existing is not None:
                hashes = self._remote_hash(ftp, remote)
                if hashes["sha256"] == expected_sha256:
                    return {"state": "transferred", "remote": remote, "reused": True, "verified_by": "full_retrieve_sha256", **hashes}
                raise TransportError("REMOTE_COLLISION", "This remote filename already holds different content. Use a new job identifier.")
            with local.open("rb") as stream:
                # Hash the opened descriptor as well; later reads stay bound to this exact file.
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != expected_sha256:
                    raise TransportError("ARTIFACT_CHANGED", "The opened artifact differs from the validated SHA256.")
                stream.seek(0)
                attempt["stor_started"] = True
                ftp.storbinary("STOR " + remote, stream, blocksize=256 * 1024)
            hashes = self._remote_hash(ftp, remote)
            if hashes["sha256"] != expected_sha256:
                raise TransportError("TRANSFER_CORRUPT", "The retrieved remote SHA256 differs from the validated artifact. Do not start it.")
            return {"state": "transferred", "remote": remote, "verified_by": "full_retrieve_sha256", **hashes}

    def start(self, remote: str, plate: int = 1, job_name: str = "", options: dict | None = None) -> dict:
        self._write_supported("start")
        remote = _remote_path(remote)
        if not remote.lower().endswith(".3mf"):
            raise TransportError("INVALID_PRINT_FILE", "This adapter starts inspected sliced 3MF artifacts only.")
        if type(plate) is not int or not 1 <= plate <= 100:
            raise TransportError("INVALID_PLATE", "The internal Bambu plate number is one-based and must be between 1 and 100.")
        options = dict(options or {})
        expected = options.get("sha256", options.get("expected_sha256", ""))
        if not re.fullmatch(r"[a-f0-9]{64}", str(expected)):
            raise TransportError("MISSING_CHECKSUM", "Starting requires the inspected artifact's SHA256 in options.sha256.")
        with self._ftp() as ftp:
            hashes = self._remote_print_evidence(ftp, remote, plate)
        if hashes["sha256"] != expected:
            raise TransportError("REMOTE_CHANGED", "Remote artifact changed after upload. Starting has been refused.")
        if options.get("md5") and options["md5"] != hashes["gcode_md5"]:
            raise TransportError("PLATE_CHECKSUM", "The selected remote plate G-code MD5 differs from the inspected plate.")
        self._ready(self.snapshot())
        bed_type = options.get("bed_type", self.printer.get("plate_type"))
        if bed_type not in {"textured_plate", "hot_plate", "cool_plate", "engineering_plate"}:
            raise TransportError("PLATE_UNCONFIRMED", "Configure and confirm the installed plate before starting.")
        use_ams = options.get("use_ams", False)
        mapping = options.get("ams_mapping", [])
        if type(use_ams) is not bool or not isinstance(mapping, list) or any(type(v) is not int or v not in {*range(-1, 16), 254} for v in mapping):
            raise TransportError("AMS_MAPPING", "Specify a valid AMS boolean and verified zero-based tray mapping.")
        if use_ams and not mapping:
            raise TransportError("AMS_MAPPING", "AMS printing requires an explicit verified tray mapping.")
        if not use_ams:
            if hashes["filament_count"] != 1:
                raise TransportError("AMS_MAPPING", "External-spool automation currently requires a single project filament.")
            mapping = [254]
        if len(mapping) < hashes["filament_count"]:
            raise TransportError("AMS_MAPPING", "The physical tray mapping does not cover every project filament.")
        mapping = mapping + [-1] * max(0, 5 - len(mapping))
        basename = PurePosixPath(remote).name
        derived_name = basename[:-10] if basename.lower().endswith(".gcode.3mf") else basename[:-4]
        job_name = job_name or derived_name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,150}", job_name):
            raise TransportError("INVALID_JOB_NAME", "Use a unique ASCII job name without control characters.")
        seq = str(int(time.time() * 1000) % 2_147_483_647)
        legacy = remote.lower().endswith(".gcode.3mf")
        if legacy and (use_ams or hashes["plate_paths"] != ["Metadata/plate_1.gcode"] or hashes["legacy_selects_filament"]):
            raise TransportError("LEGACY_CONTAINER", "Use a sliced .3mf project_file job for remapped filaments, AMS or multiple plates. Legacy .gcode.3mf only supports a single external-spool plate without project filament selectors.")
        if legacy and job_name != derived_name:
            raise TransportError("INVALID_JOB_NAME", "Legacy gcode_file derives its task name from the remote filename.")
        fields = {
            "sequence_id": seq, "command": "project_file", "param": f"Metadata/plate_{plate}.gcode",
            "url": "file:///sdcard/" + remote, "subtask_name": job_name, "md5": hashes["gcode_md5"],
            "bed_type": bed_type, "bed_leveling": True, "flow_cali": bool(options.get("flow_cali", False)),
            "vibration_cali": bool(options.get("vibration_cali", True)), "layer_inspect": False,
            "timelapse": False, "use_ams": use_ams, "ams_mapping": mapping,
            "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
        }
        if legacy:
            fields = {"sequence_id": seq, "command": "gcode_file", "param": remote}
        payload = {"print": fields}
        def matching_reply(packet):
            reply = packet["payload"].get("print", {})
            return str(reply.get("sequence_id")) == seq and reply.get("command") == fields["command"]
        packets = self._exchange([payload], lambda ps: any(matching_reply(p) for p in ps),
                                 min(self.timeout, 6), mutation=True)
        ack = next((p["payload"]["print"] for p in packets if matching_reply(p)), None)
        return {"state": "start_command_sent", "sequence_id": seq, "remote": remote, "job_name": job_name,
                "received_at": time.time(), "ack": ack, "printer_started": False,
                "command": fields["command"], "gcode_md5": hashes["gcode_md5"],
                "note": "Publish/ack is transport evidence only. Reconcile exact filename and fresh preparation/layer telemetry."}

    def command(self, action: str, **kwargs) -> dict:
        self._write_supported(action)
        state = self.snapshot()
        if action in {"pause", "cancel"}:
            # Emergency pause/stop must remain usable even if HMS prevents a resume.
            if state.get("model") != self.model or not state.get("identity_verified"):
                raise TransportError("PRINTER_IDENTITY", "Live printer identity does not match the configured device.")
            if state.get("gcode_state") not in _ACTIVE:
                raise TransportError("NO_ACTIVE_JOB", "There is no active printer job for this command.")
            fields = {"command": "pause" if action == "pause" else "stop"}
        elif action == "resume":
            self._ready(state, idle=False)
            if state.get("gcode_state") != "PAUSE":
                raise TransportError("NOT_PAUSED", "Only a paused printer can resume.")
            if kwargs.get("cause_resolved") is not True:
                raise TransportError("CAUSE_UNRESOLVED", "Confirm the pause's physical cause is resolved before resuming.")
            fields = {"command": "resume"}
        elif action == "temperature":
            component = kwargs.get("component")
            target = kwargs.get("temperature")
            if component not in {"bed", "nozzle"} or type(target) not in {int, float} or not math.isfinite(target):
                raise TransportError("INVALID_TEMPERATURE", "Choose bed or nozzle and a finite temperature in Celsius.")
            maximum = 80 if component == "bed" else 300
            if not 0 <= target <= maximum:
                raise TransportError("INVALID_TEMPERATURE", f"The A1 mini {component} limit for this adapter is {maximum} C.")
            self._ready(state, idle=True)
            if target > 0 and component == "nozzle":
                material = str(kwargs.get("material", "")).upper()
                ranges = {"PLA": (170, 250), "PETG": (220, 280), "TPU": (190, 260)}
                if material not in ranges or not ranges[material][0] <= target <= ranges[material][1]:
                    raise TransportError("MATERIAL_TEMPERATURE", "Declare the loaded PLA, PETG or TPU and use its validated temperature range.")
            fields = {"command": "gcode_line", "param": f"{'M140' if component == 'bed' else 'M104'} S{target:g}\n"}
        elif action == "fan":
            self._ready(state, idle=False)
            if kwargs.get("fan", "part") not in {"part", 1, "1"}:
                raise TransportError("UNSUPPORTED_OPERATION", "A1 mini exposes the part cooling fan; no auxiliary or chamber fan is enabled.")
            speed = kwargs.get("speed")
            if type(speed) not in {int, float} or not math.isfinite(speed) or not 0 <= speed <= 100:
                raise TransportError("INVALID_FAN_SPEED", "Part cooling fan speed must be between 0 and 100 percent.")
            fields = {"command": "gcode_line", "param": f"M106 P1 S{round(speed * 255 / 100)}\n"}
        elif action == "speed":
            self._ready(state, idle=False)
            if state.get("gcode_state") not in {"RUNNING", "PAUSE"}:
                raise TransportError("NO_ACTIVE_JOB", "Speed mode applies only to an active print.")
            mode = kwargs.get("mode")
            mode = {"silent": 1, "standard": 2, "sport": 3, "ludicrous": 4}.get(mode, mode)
            if type(mode) is not int or mode not in {1, 2, 3, 4}:
                raise TransportError("INVALID_SPEED", "Choose silent, standard, sport or ludicrous.")
            fields = {"command": "print_speed", "param": str(mode)}
        else:
            raise TransportError("UNSUPPORTED_OPERATION", "This operation has no enabled device implementation.")
        seq = str(int(time.time() * 1000) % 2_147_483_647)
        fields["sequence_id"] = seq
        self._exchange([{"print": fields}], lambda ps: any(str(p["payload"].get("print", {}).get("sequence_id")) == seq
                                                           and p["payload"].get("print", {}).get("command") == fields["command"] for p in ps),
                       min(5, self.timeout), mutation=True)
        return {"state": "command_sent", "action": action, "sequence_id": seq, "confirmed_effect": False}

    @staticmethod
    def _recv_exact(stream, size: int) -> bytes:
        output = bytearray()
        while len(output) < size:
            part = stream.recv(size - len(output))
            if not part:
                raise TransportError("CAMERA_TRUNCATED", "The camera connection ended before a complete frame arrived.")
            output.extend(part)
        return bytes(output)

    def camera(self, output: Path) -> dict:
        self._write_supported("camera")
        packet = struct.pack("<IIII", 64, 0x3000, 0, 0) + b"bblp".ljust(32, b"\0") + self._code.encode("ascii").ljust(32, b"\0")
        try:
            with socket.create_connection((self.host, 6000), timeout=self.timeout) as plain:
                with self._context().wrap_socket(plain, server_hostname=self.host) as stream:
                    self._pin(stream)
                    stream.sendall(packet)
                    header = self._recv_exact(stream, 16)
                    size = struct.unpack_from("<I", header)[0]
                    if not 4 <= size <= 5_000_000:
                        raise TransportError("CAMERA_PROTOCOL", "Camera returned an invalid frame length. Check access code and camera availability.")
                    jpeg = self._recv_exact(stream, size)
                    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                        raise TransportError("CAMERA_PROTOCOL", "Camera frame is not a complete JPEG.")
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as file:
                file.write(jpeg)
            return {"path": str(output.resolve()), "bytes": len(jpeg), "received_at": time.time(), "format": "image/jpeg",
                    "bed_clear_verified": False}
        except BaseException as exc:
            raise self._safe_error("CAMERA_ERROR", exc) from None

    def probe(self) -> dict:
        result = {"capabilities": self.capabilities(), "tls_verify": self.tls_verify,
                  "tls_pinned": bool(self.fingerprint), "start_command_tested": False}
        for name, operation in [("telemetry", self.snapshot), ("files", self.list_files)]:
            try:
                value = operation()
                result[name] = {"ok": True, "result": value}
            except TransportError as exc:
                result[name] = {"ok": False, "code": exc.code, "message": str(exc)}
        return result
