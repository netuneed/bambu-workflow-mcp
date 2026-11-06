"""Bootstrap must verify all executable input before installation or local setup."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import urllib.request
import zipfile

import pytest


SOURCE = Path(__file__).parents[1]/"skills/bambu-print/scripts/bootstrap.py"
spec = importlib.util.spec_from_file_location("lab_bootstrap", SOURCE)
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def fixture_bundle(tmp_path, *, target="windows", extra=None, tamper=False):
    wheel = "wheel/bambu_workflow_mcp-0.5.0-py3-none-any.whl"
    members = {wheel: b"owned test wheel; never execute",
               "requirements.lock.txt": b"# test\n", "scripts/install.ps1": b"# owned\n",
               "scripts/install.sh": b"# owned\n", "scripts/call_mcp.py": b"# owned\n",
               "skills/bambu-print/SKILL.md": b"# owned\n",
               "examples/acceptance-coupon-12x12x1.2mm.stl": b"owned test mesh"}
    members["release.json"] = json.dumps({"package":"bambu-workflow-mcp","version":"0.5.0",
        "target_platform":target,"contains_personal_configuration":False,
        "wheel_sha256":hashlib.sha256(members[wheel]).hexdigest()}).encode()
    members["SHA256SUMS.txt"] = "".join(hashlib.sha256(data).hexdigest()+"  "+name+"\n"
        for name,data in members.items()).encode()
    if tamper:
        members["scripts/install.ps1"] = b"changed executable"
    path=tmp_path/"owned.zip"
    with zipfile.ZipFile(path,"w") as z:
        for name,data in members.items():z.writestr(bootstrap.PREFIX+"/"+name,data)
        if extra:
            name, mode = extra
            info=zipfile.ZipInfo(name)
            if mode:info.external_attr=mode<<16
            z.writestr(info,b"owned malicious fixture")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_manifest_and_private_extraction(tmp_path):
    path,digest=fixture_bundle(tmp_path)
    files=bootstrap.validate_bundle(path,digest,"windows")
    out=bootstrap.unpack(path,tmp_path/"new-private-stage",files)
    assert (out/"scripts/install.ps1").read_bytes()==b"# owned\n"
    assert len(list(p for p in out.rglob("*") if p.is_file()))==len(files)


@pytest.mark.parametrize("reason",["hash","tamper","platform","extra"])
def test_rejects_substitution_or_unlisted_executable(tmp_path,reason):
    path,digest=fixture_bundle(tmp_path,tamper=reason=="tamper",
        extra=(bootstrap.PREFIX+"/unexpected.py",None) if reason=="extra" else None)
    with pytest.raises(ValueError):
        bootstrap.validate_bundle(path,"0"*64 if reason=="hash" else digest,
                                  "macos" if reason=="platform" else "windows")


@pytest.mark.parametrize("name,mode",[
    ("../escape.ps1",None), ("/absolute.ps1",None),
    (bootstrap.PREFIX+"/../escape.ps1",None),
    (bootstrap.PREFIX+"/a\\escape.ps1",None),
    (bootstrap.PREFIX+"/C:escape.ps1",None),
    (bootstrap.PREFIX+"/CON",None), (bootstrap.PREFIX+"/nul.txt",None),
    (bootstrap.PREFIX+"/a./escape.ps1",None),
    (bootstrap.PREFIX+"/a//escape.ps1",None),
    (bootstrap.PREFIX+"/link",stat.S_IFLNK|0o777),
    (bootstrap.PREFIX+"/RELEASE.JSON",None),
])
def test_rejects_paths_and_links_before_extraction(tmp_path,name,mode):
    path,digest=fixture_bundle(tmp_path,extra=(name,mode))
    with pytest.raises(ValueError):bootstrap.validate_bundle(path,digest,"windows")
    assert not (tmp_path/"escape.ps1").exists()


def test_archive_expansion_is_bounded(tmp_path,monkeypatch):
    path,digest=fixture_bundle(tmp_path)
    monkeypatch.setattr(bootstrap,"MAX_EXPANDED",10)
    with pytest.raises(ValueError,match="expanded size"):
        bootstrap.validate_bundle(path,digest,"windows")


def test_manifest_duplicate_is_refused():
    with pytest.raises(ValueError):bootstrap.manifest_entries(("a"*64+"  file\n") .encode()*2)


@pytest.mark.parametrize("url",["http://github.com/file","https://example.com/file","https://github.com@evil.example/file"])
def test_rejects_non_github_download_before_network(url):
    with pytest.raises(ValueError):bootstrap.fetch(url,1024)


def test_redirect_does_not_leave_https_github():
    request=urllib.request.Request("https://github.com/release")
    with pytest.raises(ValueError):
        bootstrap.GitHubRedirects().redirect_request(request,None,302,"",{},"https://evil.example/payload")


@pytest.mark.parametrize("mutation",["url","digest","duplicate"])
def test_asset_identity_is_exact(mutation):
    name=bootstrap.PREFIX+"-windows.zip"
    asset={"name":name,"browser_download_url":f"https://github.com/{bootstrap.REPOSITORY}/releases/download/v{bootstrap.VERSION}/{name}","digest":"sha256:"+"a"*64}
    if mutation=="url":asset["browser_download_url"]=asset["browser_download_url"].replace("v0.5.0","latest")
    if mutation=="digest":asset["digest"]=None
    release={"assets":[asset,asset] if mutation=="duplicate" else [asset]}
    with pytest.raises(ValueError):bootstrap.release_asset(release,name)


def test_download_checks_github_digest_and_independent_release_manifest(tmp_path,monkeypatch):
    path,digest=fixture_bundle(tmp_path)
    name=bootstrap.PREFIX+"-windows.zip"
    sumsname="SHA256SUMS-0.5.0.txt"
    sums=(digest+"  "+name+"\n").encode()
    base=f"https://github.com/{bootstrap.REPOSITORY}/releases/download/v0.5.0/"
    assets=[{"name":n,"browser_download_url":base+n,"digest":"sha256:"+hashlib.sha256(data).hexdigest(),"size":len(data)}
            for n,data in ((name,path.read_bytes()),(sumsname,sums))]
    release={"tag_name":"v0.5.0","draft":False,"prerelease":False,"assets":assets}
    responses={f"https://api.github.com/repos/{bootstrap.REPOSITORY}/releases/tags/v0.5.0":json.dumps(release).encode(),
               base+name:path.read_bytes(),base+sumsname:sums}
    calls=[]
    def fetch(url,limit):calls.append(url);return responses[url]
    monkeypatch.setattr(bootstrap,"fetch",fetch)
    assert bootstrap.download_bundle("windows")== (path.read_bytes(),digest)
    assert len(calls)==3
    responses[base+sumsname]=b"tampered checksum manifest"
    with pytest.raises(ValueError,match="digest"):
        bootstrap.download_bundle("windows")


def test_windows_arguments_are_not_shell_text(tmp_path,monkeypatch):
    monkeypatch.setattr(bootstrap.shutil,"which",lambda _:"powershell.exe")
    args=argparse.Namespace(install_root=tmp_path/"spaces ` and $ here",config=None,skill_root=None,install_skill=False,resume=False)
    command=bootstrap.installer_command(tmp_path,args,"windows")
    assert command[command.index("-InstallRoot")+1]==str(args.install_root.resolve())
    assert command.count("-File")==1
    assert "-InstallSkill" not in command


def test_setup_rejects_noninteractive_secret_entry_before_network(monkeypatch):
    monkeypatch.setattr(sys,"argv",[str(SOURCE),"--onboard"])
    monkeypatch.setattr(sys,"stdin",SimpleNamespace(isatty=lambda:False))
    monkeypatch.setattr(bootstrap,"fetch",lambda *a:pytest.fail("must not download before TTY check"))
    with pytest.raises(SystemExit) as result:bootstrap.main()
    assert result.value.code==2


def test_offline_bootstrap_only_installs_then_returns_onboard_command(tmp_path,monkeypatch,capsys):
    path,digest=fixture_bundle(tmp_path)
    monkeypatch.setattr(sys,"argv",[str(SOURCE),"--bundle",str(path),"--sha256",digest])
    monkeypatch.setattr(bootstrap,"platform_name",lambda:"windows")
    monkeypatch.setattr(bootstrap,"installer_command",lambda *a:["owned-installer"])
    monkeypatch.setattr(bootstrap,"fetch",lambda *a:pytest.fail("offline must not download"))
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0,stdout='No broken requirements found.\n'+json.dumps({"python":str(tmp_path/"runtime/.venv/Scripts/python.exe")}),stderr="")
    monkeypatch.setattr(bootstrap.subprocess,"run",run)
    assert bootstrap.main()==0
    report=json.loads(capsys.readouterr().out)
    assert calls==[["owned-installer"]]
    assert report["printer_contacted"] is False and report["codex_modified"] is False
    assert report["onboard_command"][-1]=="onboard"
    assert report["runtime_dir"]==str(tmp_path/"runtime")


def test_bad_archive_never_executes_installer(tmp_path,monkeypatch,capsys):
    path,digest=fixture_bundle(tmp_path,tamper=True)
    monkeypatch.setattr(sys,"argv",[str(SOURCE),"--bundle",str(path),"--sha256",digest])
    monkeypatch.setattr(bootstrap,"platform_name",lambda:"windows")
    monkeypatch.setattr(bootstrap.subprocess,"run",lambda *a,**k:pytest.fail("unverified installer must not run"))
    assert bootstrap.main()==1
    assert json.loads(capsys.readouterr().err)["print_started"] is False
