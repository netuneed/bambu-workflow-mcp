"""Exercise actual protocol builders and decoders with bounded local fakes only."""
import hashlib
import subprocess
import time
from types import SimpleNamespace

import pytest

from bambu_workflow.models import get_model
from bambu_workflow.transport import BambuLAN, TransportError, decode_tool_telemetry
from test_transport import MemoryFTP, sliced, use_ftp


def device(model, **overrides):
    spec = get_model(model)
    return BambuLAN({"id":model,"model":model,"host":"192.0.2.1","serial":"NEWTESTDEVICE001",
                     "tls_verify":False,"nozzle_diameter":0.4,"nozzle_type":spec["default_nozzle_type"],
                     "plate_type":"textured_plate",**overrides},"test-code")


def idle_for(adapter):
    return {"model":adapter.model,"identity_verified":True,"received_at":time.time(),"gcode_state":"IDLE",
            "hms":[],"print_error":0,"nozzle_diameter":"0.4","nozzle_type":adapter.printer["nozzle_type"],"raw":{}}


@pytest.mark.parametrize("model,remote,url", [
    ("a1mini","cache/job.3mf","file:///sdcard/cache/job.3mf"),
    ("a1","job.3mf","file:///sdcard/job.3mf"),
    ("p1p","cache/job.3mf","file:///sdcard/cache/job.3mf"),
    ("p1s","cache/job.3mf","file:///sdcard/cache/job.3mf"),
    ("x1","cache/job.3mf","file:///sdcard/cache/job.3mf"),
    ("x1c","cache/job.3mf","file:///sdcard/cache/job.3mf"),
    ("x1e","cache/job.3mf","file:///sdcard/cache/job.3mf"),
    ("p2s","cache/job.3mf","ftp:///cache/job.3mf"),
    ("h2s","job.3mf","ftp:///job.3mf"),
    ("a2l","cache/job.3mf","file:///sdcard/cache/job.3mf"),
])
def test_single_tool_model_uses_its_own_storage_and_url_without_republishing(monkeypatch, model, remote, url):
    adapter = device(model)
    data = sliced()
    assert adapter.remote_path("job.3mf") == remote
    use_ftp(monkeypatch, adapter, MemoryFTP({remote:data}))
    monkeypatch.setattr(adapter,"snapshot",lambda:idle_for(adapter))
    requests = []
    monkeypatch.setattr(adapter,"_exchange",lambda *args,**kwargs:requests.extend(args[0]) or [])
    result = adapter.start(remote, options={"sha256":hashlib.sha256(data).hexdigest()})
    assert len(requests) == 1 and requests[0]["print"]["url"] == url
    assert requests[0]["print"]["command"] == "project_file"
    assert result["printer_started"] is False


def test_modern_ams_mapping_is_derived_from_the_verified_absolute_tray(monkeypatch):
    adapter = device("p2s")
    data = sliced();remote = adapter.remote_path("job.3mf")
    use_ftp(monkeypatch,adapter,MemoryFTP({remote:data}))
    monkeypatch.setattr(adapter,"snapshot",lambda:idle_for(adapter))
    requests=[]
    monkeypatch.setattr(adapter,"_exchange",lambda *args,**kwargs:requests.extend(args[0]) or [])
    adapter.start(remote,options={"sha256":hashlib.sha256(data).hexdigest(),"use_ams":True,"ams_mapping":[7]})
    assert requests[0]["print"]["ams_mapping"] == [7]
    assert requests[0]["print"]["ams_mapping2"] == [{"ams_id":1,"slot_id":3}]


@pytest.mark.parametrize("model", ["h2d","h2dpro","h2c","x2d"])
def test_dual_tool_launch_is_rejected_before_any_network_io(monkeypatch, model):
    adapter = device(model)
    monkeypatch.setattr(adapter,"_ftp",lambda:pytest.fail("no FTP on unsupported launch"))
    monkeypatch.setattr(adapter,"_exchange",lambda *args,**kwargs:pytest.fail("no MQTT on unsupported launch"))
    with pytest.raises(TransportError,match="physical-nozzle"):
        adapter.start(adapter.remote_path("job.3mf"))


def test_capability_overrides_can_disable_but_cannot_fabricate_operations():
    assert device("p1s",capability_overrides={"start":False}).capabilities()["start"] is False
    with pytest.raises(TransportError,match="cannot enable"):
        device("x2d",capability_overrides={"start":True}).capabilities()


def test_legacy_missing_nozzle_needs_explicit_setup_but_known_mismatch_never_falls_back():
    adapter=device("p1s",nozzle_confirmed=True);status=idle_for(adapter)
    status.update(nozzle_diameter=None,nozzle_type=None)
    adapter._ready(status)
    adapter.printer["nozzle_confirmed"]=False
    with pytest.raises(TransportError,match="diameter"):
        adapter._ready(status)
    adapter.printer["nozzle_confirmed"]=True;status["nozzle_diameter"]="0.6"
    with pytest.raises(TransportError,match="diameter"):
        adapter._ready(status)


def test_modern_nozzle_rack_and_extruder_temperatures_are_actual_reported_facts():
    result=decode_tool_telemetry({"device":{
        "nozzle":{"info":[{"id":0,"diameter":0.6,"type":"HH01"},{"id":16,"diameter":0.4,"type":"HS01"}]},
        "extruder":{"info":[{"id":0,"temp":173 | (230<<16),"info":8,"hnow":0,"snow":65280}]}}})
    assert result["nozzle_diameter"] == 0.6
    assert result["nozzle_type"] == "hardened_steel" and result["nozzle_flow"] == "high_flow"
    assert result["nozzles"][1]["position"] == "rack"
    assert result["extruders"][0]["temperature"] == 173 and result["extruders"][0]["target"] == 230
    assert decode_tool_telemetry({})["nozzle_diameter"] is None


def test_snapshot_keeps_nested_deltas_and_never_uses_configured_serial_or_nozzle(monkeypatch):
    adapter=device("h2s")
    packets=[{"received_at":time.time(),"payload":{"print":{"command":"push_status","gcode_state":"IDLE","device":{"nozzle":{"info":[{"id":0,"diameter":0.6,"type":"HS01"}]}}}}},
             {"received_at":time.time(),"payload":{"print":{"command":"push_status","device":{"extruder":{"info":[{"id":0,"temp":25,"info":8,"hnow":0}]}}}}},
             {"received_at":time.time(),"payload":{"info":{"command":"get_version","module":[{"name":"ota","product_name":"Bambu Lab H2S","sw_ver":"01.02.00.00"}]}}}]
    monkeypatch.setattr(adapter,"_exchange",lambda *args,**kwargs:packets)
    status=adapter.snapshot()
    assert status["model"] == "h2s" and status["identity_verified"] is False
    assert status["nozzle_diameter"] == 0.6
    assert status["nozzle_diameter"] != adapter.printer["nozzle_diameter"]


def test_h2s_and_p1s_thermal_controls_use_distinct_manufacturer_limits(monkeypatch):
    adapter=device("h2s");monkeypatch.setattr(adapter,"snapshot",lambda:idle_for(adapter));sent=[]
    monkeypatch.setattr(adapter,"_exchange",lambda *args,**kwargs:sent.extend(args[0]) or [])
    assert adapter.command("temperature",component="bed",temperature=120)["confirmed_effect"] is False
    assert sent[0]["print"]["param"] == "M140 S120\n"
    adapter=device("p1s");monkeypatch.setattr(adapter,"snapshot",lambda:idle_for(adapter))
    monkeypatch.setattr(adapter,"_exchange",lambda *args,**kwargs:pytest.fail("must reject before publish"))
    with pytest.raises(TransportError,match="100"):
        adapter.command("temperature",component="bed",temperature=110)


def test_rtsp_snapshot_is_bounded_and_does_not_log_credentials(monkeypatch,tmp_path):
    adapter=device("x1c",ffmpeg_path="/configured/ffmpeg");calls=[]
    def capture(arguments,**kwargs):
        calls.append((arguments,kwargs))
        return SimpleNamespace(returncode=0,stdout=b"\xff\xd8jpeg\xff\xd9",stderr=b"")
    monkeypatch.setattr(subprocess,"run",capture)
    result=adapter.camera(tmp_path/"frame.jpg")
    assert result["transport"] == "rtsps322" and result["bed_clear_verified"] is False
    assert "test-code" not in str(result)
    assert calls[0][1]["timeout"] == adapter.timeout+2
    def timeout(arguments,**kwargs):raise subprocess.TimeoutExpired(arguments,adapter.timeout)
    monkeypatch.setattr(subprocess,"run",timeout)
    with pytest.raises(TransportError) as error:adapter.camera(tmp_path/"another.jpg")
    assert "test-code" not in str(error.value) and error.value.code == "CAMERA_TIMEOUT"


def test_rtsp_pin_policy_is_not_silently_dropped(monkeypatch,tmp_path):
    adapter=device("p2s",tls_fingerprint="f"*64,ffmpeg_path="/configured/ffmpeg")
    monkeypatch.setattr(subprocess,"run",lambda *args,**kwargs:pytest.fail("unsupported pin policy must stop before launch"))
    with pytest.raises(TransportError,match="fingerprint"):
        adapter.camera(tmp_path/"frame.jpg")


@pytest.mark.parametrize("verify", [True,False])
def test_rtsp_tls_configuration_is_explicit_in_the_backend(monkeypatch,tmp_path,verify):
    adapter=device("x1c",tls_verify=verify,tls_ca_file="/configured/printer-ca.pem",ffmpeg_path="/configured/ffmpeg")
    calls=[]
    def capture(arguments,**kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0,stdout=b"\xff\xd8jpeg\xff\xd9",stderr=b"")
    monkeypatch.setattr(subprocess,"run",capture)
    adapter.camera(tmp_path/"frame.jpg")
    assert calls[0][calls[0].index("-tls_verify")+1] == ("1" if verify else "0")
    assert ("-ca_file" in calls[0]) is verify


def test_partial_client_certificate_configuration_fails_before_any_backend():
    with pytest.raises(TransportError,match="both client certificate"):
        device("x1e",tls_client_cert="/configured/certificate.pem")
