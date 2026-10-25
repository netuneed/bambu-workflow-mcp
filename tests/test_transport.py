"""Protocol boundary failures without contacting or moving hardware."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import time
import zipfile

import pytest

from bambu_workflow.transport import BambuLAN, TransportError, _ImplicitFTPS, _remote_path


@pytest.fixture
def printer():
    return BambuLAN({"id": "test", "host": "192.0.2.1", "serial": "030TESTPRINTER01", "model": "a1mini",
                     "nozzle_diameter": 0.4, "nozzle_type": "stainless_steel", "tls_verify": False,
                     "plate_type": "textured_plate"}, "test-code")


def idle():
    return {"received_at": time.time(), "model": "a1mini", "identity_verified": True, "gcode_state": "IDLE",
            "nozzle_diameter": "0.4", "nozzle_type": "stainless_steel", "hms": [], "print_error": 0}


def sliced(gcode=b"; filament_colour = #000000\nM104 S220\nG1 X1 Y1 E1\n", plate=1):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(f"Metadata/plate_{plate}.gcode", gcode)
    return data.getvalue()


@pytest.mark.parametrize("value", ["/cache/file.3mf", "cache/../file.3mf", "cache/x\r\nDELE x", "cache\\x", "cache//x", "other/x"])
def test_remote_path_rejects_command_injection_and_traversal(value):
    with pytest.raises(TransportError):
        _remote_path(value)


@pytest.mark.parametrize("field,value,code", [
    ("received_at", 0, "STALE_TELEMETRY"),
    ("model", "a1", "PRINTER_IDENTITY"),
    ("identity_verified", False, "PRINTER_IDENTITY"),
    ("gcode_state", "RUNNING", "PRINTER_BUSY"),
    ("nozzle_diameter", "0.6", "NOZZLE_MISMATCH"),
    ("nozzle_type", "hardened_steel", "NOZZLE_MISMATCH"),
    ("hms", [{"code": 1}], "PRINTER_ERROR"),
])
def test_preflight_rejects_stale_busy_mismatched_or_blocked_state(printer, field, value, code):
    status = idle()
    status[field] = value
    with pytest.raises(TransportError) as error:
        printer._ready(status)
    assert error.value.code == code


def test_unknown_model_is_read_only():
    transport = BambuLAN({"host": "192.0.2.1", "serial": "039TESTPRINTER01", "model": "a1"}, "test-code")
    with pytest.raises(TransportError, match="not enabled"):
        transport.start("cache/test.gcode.3mf")


class MemoryFTP:
    def __init__(self, existing=None, corrupt=False, interrupt=False, completed_timeout=False):
        self.files = dict(existing or {})
        self.corrupt = corrupt
        self.interrupt = interrupt
        self.completed_timeout = completed_timeout
        self.writes = 0

    def voidcmd(self, command):
        return "200 OK"

    def size(self, path):
        import ftplib
        if path not in self.files:
            raise ftplib.error_perm("550 not found")
        return len(self.files[path])

    def storbinary(self, command, stream, blocksize):
        self.writes += 1
        data = stream.read()
        if self.interrupt:
            self.files[command[5:]] = data[:len(data) // 2]
            raise ConnectionResetError("interrupted transfer")
        self.files[command[5:]] = b"corrupt" if self.corrupt else data
        if self.completed_timeout:
            raise TimeoutError("control completion missing after full upload")

    def retrbinary(self, command, callback, blocksize):
        callback(self.files[command[5:]])


def use_ftp(monkeypatch, printer, ftp):
    @contextlib.contextmanager
    def connect():
        yield ftp
    monkeypatch.setattr(printer, "_ftp", connect)


def test_upload_verifies_full_remote_content_and_exact_repeat_is_safe(printer, monkeypatch, tmp_path):
    local = tmp_path / "print.gcode.3mf"
    local.write_bytes(b"validated sliced file")
    sha = hashlib.sha256(local.read_bytes()).hexdigest()
    ftp = MemoryFTP()
    use_ftp(monkeypatch, printer, ftp)
    result = printer.upload(local, "cache/unique.gcode.3mf", sha)
    assert result["verified_by"] == "full_retrieve_sha256"
    assert printer.upload(local, "cache/unique.gcode.3mf", sha)["reused"] is True
    assert ftp.writes == 1


def test_upload_collision_never_overwrites(printer, monkeypatch, tmp_path):
    local = tmp_path / "test"
    local.write_bytes(b"new")
    ftp = MemoryFTP({"cache/unique.gcode.3mf": b"previous file"})
    use_ftp(monkeypatch, printer, ftp)
    with pytest.raises(TransportError) as error:
        printer.upload(local, "cache/unique.gcode.3mf", hashlib.sha256(b"new").hexdigest())
    assert error.value.code == "REMOTE_COLLISION"
    assert ftp.writes == 0


def test_corrupt_upload_is_not_reported_transferred(printer, monkeypatch, tmp_path):
    local = tmp_path / "test"
    local.write_bytes(b"data")
    use_ftp(monkeypatch, printer, MemoryFTP(corrupt=True))
    with pytest.raises(TransportError) as error:
        printer.upload(local, "cache/unique.gcode.3mf", hashlib.sha256(b"data").hexdigest())
    assert error.value.code == "TRANSFER_CORRUPT"


def test_local_change_after_validation_prevents_upload(printer, monkeypatch, tmp_path):
    local = tmp_path / "test"
    local.write_bytes(b"changed")
    ftp = MemoryFTP()
    use_ftp(monkeypatch, printer, ftp)
    with pytest.raises(TransportError) as error:
        printer.upload(local, "cache/unique.gcode.3mf", hashlib.sha256(b"old").hexdigest())
    assert error.value.code == "ARTIFACT_CHANGED"
    assert ftp.writes == 0


def test_upload_recovers_lost_completion_by_retrieve_without_repeating_stor(printer, monkeypatch, tmp_path):
    local = tmp_path / "test"
    local.write_bytes(b"the complete valid artifact")
    ftp = MemoryFTP(completed_timeout=True)
    use_ftp(monkeypatch, printer, ftp)
    result = printer.upload(local, "cache/unique.3mf", hashlib.sha256(local.read_bytes()).hexdigest())
    assert result["control_completion_uncertain"] is True
    assert result["verified_by"] == "full_retrieve_sha256"
    assert ftp.writes == 1


def test_interrupted_upload_is_never_retried_or_marked_transferred(printer, monkeypatch, tmp_path):
    local = tmp_path / "test"
    local.write_bytes(b"the complete valid artifact")
    ftp = MemoryFTP(interrupt=True)
    use_ftp(monkeypatch, printer, ftp)
    with pytest.raises(TransportError) as error:
        printer.upload(local, "cache/unique.3mf", hashlib.sha256(local.read_bytes()).hexdigest())
    assert error.value.code == "TRANSFER_INCOMPLETE"
    assert ftp.writes == 1


def test_stor_closes_data_socket_without_waiting_for_peer_tls_shutdown(monkeypatch):
    class DataSocket:
        data = b""
        closed = False
        def sendall(self, block): self.data += block
        def close(self): self.closed = True
        def unwrap(self): pytest.fail("TLS peer shutdown must not be awaited on Bambu STOR")
    data = DataSocket()
    ftp = _ImplicitFTPS()
    monkeypatch.setattr(ftp, "voidcmd", lambda command: "200 ok")
    monkeypatch.setattr(ftp, "transfercmd", lambda *args: data)
    monkeypatch.setattr(ftp, "voidresp", lambda: "226 done")
    assert ftp.storbinary("STOR cache/test.3mf", io.BytesIO(b"artifact")) == "226 done"
    assert data.closed and data.data == b"artifact"


def test_start_timeout_has_one_publish_and_does_not_claim_printing(printer, monkeypatch):
    data = sliced()
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.3mf": data}))
    monkeypatch.setattr(printer, "snapshot", idle)
    calls = []
    def no_reply(requests, ready, timeout, mutation):
        calls.append(requests)
        return []
    monkeypatch.setattr(printer, "_exchange", no_reply)
    result = printer.start("cache/unique.3mf", 1, "unique", {"sha256": hashlib.sha256(data).hexdigest()})
    assert len(calls) == 1
    assert result["state"] == "start_command_sent"
    assert result["printer_started"] is False
    assert result["ack"] is None
    assert calls[0][0]["print"]["param"] == "Metadata/plate_1.gcode"
    assert calls[0][0]["print"]["use_ams"] is False
    assert calls[0][0]["print"]["url"] == "file:///sdcard/cache/unique.3mf"
    assert calls[0][0]["print"]["ams_mapping"] == [254, -1, -1, -1, -1]
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert calls[0][0]["print"]["md5"] == hashlib.md5(archive.read("Metadata/plate_1.gcode")).hexdigest()


def test_start_refuses_remote_checksum_change_before_publishing(printer, monkeypatch):
    data = sliced(b"changed gcode")
    original = sliced(b"original gcode")
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.3mf": data}))
    monkeypatch.setattr(printer, "_exchange", lambda *a, **k: pytest.fail("publish must not occur"))
    with pytest.raises(TransportError) as error:
        printer.start("cache/unique.3mf", options={"sha256": hashlib.sha256(original).hexdigest()})
    assert error.value.code == "REMOTE_CHANGED"


def test_selected_nested_plate_md5_is_required_to_match_inspection(printer, monkeypatch):
    data = sliced()
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.3mf": data}))
    monkeypatch.setattr(printer, "_exchange", lambda *a, **k: pytest.fail("publish must not occur"))
    with pytest.raises(TransportError) as error:
        printer.start("cache/unique.3mf", options={"sha256": hashlib.sha256(data).hexdigest(), "md5": "0" * 32})
    assert error.value.code == "PLATE_CHECKSUM"


def test_legacy_container_refuses_remapped_project_filaments(printer, monkeypatch):
    data = sliced(b"; filament_colour = #000000\nM620 S0A\nM621 S0A\n")
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.gcode.3mf": data}))
    monkeypatch.setattr(printer, "snapshot", idle)
    monkeypatch.setattr(printer, "_exchange", lambda *a, **k: pytest.fail("publish must not occur"))
    with pytest.raises(TransportError) as error:
        printer.start("cache/unique.gcode.3mf", options={"sha256": hashlib.sha256(data).hexdigest()})
    assert error.value.code == "LEGACY_CONTAINER"


def test_simple_external_legacy_container_uses_gcode_file_command(printer, monkeypatch):
    data = sliced()
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.gcode.3mf": data}))
    monkeypatch.setattr(printer, "snapshot", idle)
    calls = []
    monkeypatch.setattr(printer, "_exchange", lambda *a, **k: calls.append(a[0]) or [])
    printer.start("cache/unique.gcode.3mf", options={"sha256": hashlib.sha256(data).hexdigest()})
    assert calls[0][0]["print"]["command"] == "gcode_file"
    assert calls[0][0]["print"]["param"] == "cache/unique.gcode.3mf"


def test_pause_can_stop_faulted_active_job_but_resume_requires_resolution(printer, monkeypatch):
    status = idle()
    status.update(gcode_state="RUNNING", hms=[{"code": 1}])
    monkeypatch.setattr(printer, "snapshot", lambda: status)
    calls = []
    monkeypatch.setattr(printer, "_exchange", lambda *args, **kwargs: calls.append(args[0]) or [])
    assert printer.command("pause")["confirmed_effect"] is False
    assert len(calls) == 1
    status.update(gcode_state="PAUSE", hms=[])
    with pytest.raises(TransportError) as error:
        printer.command("resume")
    assert error.value.code == "CAUSE_UNRESOLVED"
    assert len(calls) == 1


def test_snapshot_uses_live_ota_identity_and_stg_cur_as_stage_not_index(printer, monkeypatch):
    now = time.time()
    packets = [
        {"received_at": now, "payload": {"print": {"command": "push_status", "gcode_state": "RUNNING", "nozzle_diameter": "0.4",
                                                  "layer_num": 1, "stg_cur": 0, "stg": [2, 4, 1], "mc_print_stage": "2"}}},
        {"received_at": now, "payload": {"info": {"command": "get_version", "module": [{"name": "ota", "sn": printer.serial, "sw_ver": "01.00.00.00"}]}}},
    ]
    monkeypatch.setattr(printer, "_exchange", lambda *args, **kwargs: packets)
    status = printer.snapshot()
    assert status["model"] == "a1mini"
    assert status["identity_verified"] is True
    assert status["stage_id"] == 0
    assert status["stage"] == "printing"
    assert status["material"]["remaining_mass_verified"] is False


def test_library_errors_cannot_echo_access_codes(printer):
    error = printer._safe_error("FTPS_ERROR", RuntimeError("login failed with test-code password"))
    assert "test-code" not in str(error)
    assert "RuntimeError" in str(error)


def test_tls_reset_before_mqtt_is_distinct_from_authentication_failure(printer, monkeypatch):
    import paho.mqtt.client as mqtt
    import ssl
    class TCP:
        def settimeout(self, value): pass
    def connect(client, *args, **kwargs):
        client._ssl_wrap_socket(TCP())
    def reset(*args, **kwargs):
        raise ConnectionResetError("reset with a private network address")
    monkeypatch.setattr(mqtt.Client, "connect", connect)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", reset)
    with pytest.raises(TransportError) as error:
        printer.snapshot(timeout=1)
    assert error.value.code == "MQTT_TLS_RESET"
    assert "private network address" not in str(error.value)
    assert "test-code" not in str(error.value)


def test_read_requests_do_not_wait_for_firmware_puback(printer, monkeypatch):
    import paho.mqtt.client as mqtt
    class Reason:
        is_failure = False
    class Info:
        def wait_for_publish(self, timeout): pass
        def is_published(self): return True
    class Client:
        calls = []
        def __init__(self, *args, **kwargs): pass
        def username_pw_set(self, *args): pass
        def tls_set_context(self, *args): pass
        def tls_insecure_set(self, *args): pass
        def reconnect_delay_set(self, *args): pass
        def connect(self, *args, **kwargs): self.on_connect(self, None, {}, 0, None)
        def socket(self): return None
        def subscribe(self, *args, **kwargs): self.on_subscribe(self, None, 1, [Reason()], None)
        def loop_start(self): pass
        def publish(self, topic, body, qos, retain):
            self.calls.append(qos)
            message = type("Message", (), {"retain":False, "payload":b'{"print":{"command":"push_status","gcode_state":"IDLE"}}'})()
            self.on_message(self, None, message)
            return Info()
        def disconnect(self): pass
        def loop_stop(self): pass
    monkeypatch.setattr(mqtt, "Client", Client)
    packets = printer._exchange([{"pushing":{"command":"pushall"}}], lambda values: bool(values), timeout=1)
    assert Client.calls == [0]
    assert packets[0]["payload"]["print"]["gcode_state"] == "IDLE"


def missing_puback_client(monkeypatch, reply_mode="matching"):
    import paho.mqtt.client as mqtt
    class Reason:
        is_failure = False
    class Info:
        def wait_for_publish(self, timeout): pass
        def is_published(self): return False
    class Client:
        calls = []
        def __init__(self, *args, **kwargs): pass
        def username_pw_set(self, *args): pass
        def tls_set_context(self, *args): pass
        def tls_insecure_set(self, *args): pass
        def reconnect_delay_set(self, *args): pass
        def connect(self, *args, **kwargs): self.on_connect(self, None, {}, 0, None)
        def socket(self): return None
        def subscribe(self, *args, **kwargs): self.on_subscribe(self, None, 1, [Reason()], None)
        def loop_start(self): pass
        def publish(self, topic, body, qos, retain):
            request = json.loads(body)["print"]
            self.calls.append({"qos":qos, "request":request})
            reply = {"command":request["command"], "sequence_id":request["sequence_id"], "result":"SUCCESS"}
            if reply_mode == "wrong_sequence": reply["sequence_id"] = "unrelated"
            if reply_mode == "status_only": reply["command"] = "push_status"
            if reply_mode != "none":
                message = type("Message", (), {"retain":False, "payload":json.dumps({"print":reply}).encode()})()
                self.on_message(self, None, message)
            return Info()
        def disconnect(self): pass
        def loop_stop(self): pass
    monkeypatch.setattr(mqtt, "Client", Client)
    return Client


def test_start_preserves_matching_firmware_reply_when_broker_puback_is_lost(printer, monkeypatch):
    data = sliced()
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.3mf":data}))
    monkeypatch.setattr(printer, "snapshot", idle)
    client = missing_puback_client(monkeypatch)
    result = printer.start("cache/unique.3mf", options={"sha256":hashlib.sha256(data).hexdigest()})
    assert result["state"] == "start_command_sent"
    assert result["ack"]["command"] == "project_file"
    assert result["ack"]["result"] == "SUCCESS"
    assert result["printer_started"] is False
    assert len(client.calls) == 1
    assert client.calls[0]["qos"] == 1


@pytest.mark.parametrize("reply_mode", ["wrong_sequence", "status_only", "none"])
def test_missing_puback_without_exact_command_reply_remains_uncertain_without_republish(printer, monkeypatch, reply_mode):
    data = sliced()
    use_ftp(monkeypatch, printer, MemoryFTP({"cache/unique.3mf":data}))
    monkeypatch.setattr(printer, "snapshot", idle)
    client = missing_puback_client(monkeypatch, reply_mode)
    with pytest.raises(TransportError) as error:
        printer.start("cache/unique.3mf", options={"sha256":hashlib.sha256(data).hexdigest()})
    assert error.value.code == "MQTT_PUBLISH_UNCERTAIN"
    assert error.value.uncertain is True
    assert len(client.calls) == 1
