import sys,time,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Router

class Peer:
    def __init__(self,url):self.url=url;self.last_seen=time.monotonic();self.writes=[]
    def request(self,action,expected_url=None,**kwargs):
        if expected_url and self.url!=expected_url:raise RuntimeError('No match')
        if action=='status':return {'ok':True,'url':self.url}
        self.writes.append(action)
        return {'ok':True,'download_id':42}

class RouterTests(unittest.TestCase):
    def test_explicit_url_routes_once_and_remembers_browser(self):
        r=Router('t'*43);a,b=Peer('https://example.org/a'),Peer('https://example.org/b');r.peers=[a,b]
        r.request('save',b.url)
        self.assertEqual(a.writes,[]);self.assertEqual(b.writes,['save'])
        self.assertEqual(r.request('status')['url'],b.url)
    def test_ambiguous_auto_selection_never_broadcasts_writes(self):
        r=Router('t'*43);r.peers=[Peer('https://example.org/a'),Peer('https://example.org/b')]
        with self.assertRaises(RuntimeError):r.request('save')
        self.assertTrue(all(not p.writes for p in r.peers))

if __name__=='__main__':unittest.main()
