import time
from pathlib import Path
from tgdl.adapters.downloaders.aria2 import aria2_enabled, add_uri, tell_status

def test_rpc_add_uri():
    assert aria2_enabled()
    gid = add_uri("http://ipv4.download.thinkbroadband.com/1MB.zip", Path("./downloads"))
    assert isinstance(gid, str) and len(gid) > 0
    time.sleep(1)
    st = tell_status(gid)
    assert "status" in st
