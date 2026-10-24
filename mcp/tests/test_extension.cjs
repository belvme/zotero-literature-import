const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '..', 'bridge-background.js'), 'utf8');

function harness(options={}) {
  let listener, saves = 0, activated;
  const tab = {id: 7, active: true, url: 'https://example.org/article', title: 'Article'};
  const context = {
    browser: {
      runtime: {id:'a'.repeat(32), getURL: p=>'chrome-extension://'+'a'.repeat(32)+'/'+p,
        onMessage: {addListener: fn=>{listener=fn}}},
      tabs: {query:async q=>options.query ? options.query(q,tab) : [tab], get:async()=>({...tab}), create:()=>{},onActivated:{addListener:fn=>{activated=fn}}},
      scripting:{executeScript:async()=>[{result:options.links||[{label:'PDF下载',url:'https://example.org/paper.pdf'}]}]},
      downloads:{download:async()=>42,search:async()=>[{id:42,state:'complete',filename:'paper.pdf',bytesReceived:999}]},
      contextMenus: {remove:async()=>{}, create:()=>{}, onClicked:{addListener:()=>{}}},
    },
    Zotero: {initDeferred:{promise:Promise.resolve()}, isManifestV3:true,
      Connector:{checkIsOnline:async()=>true},
      Connector_Browser:{getTabInfo:()=>({translators:[{label:'CNKI'}]}),
        saveCurrentTabFromMCP:async()=>{saves++}}}, URL,
  };
  vm.runInNewContext(code, context);
  const sender = {id:context.browser.runtime.id, url:context.browser.runtime.getURL('mcp-bridge.html')};
  const call = (extra={}, s=sender)=>listener({type:'zoteroMcpBridge', action:'save',
    expected_url:tab.url, expires_at:Date.now()/1000+10, ...extra}, s);
  return {call, get saves(){return saves}, tab,activate:()=>activated({tabId:tab.id})};
}
test('calls the actual toolbar hook once, not a metadata-save substitute', async()=>{
  const h=harness(); const r=await h.call();
  assert.equal(r.status,'save_requested'); assert.equal(h.saves,1);
});
test('status is read only', async()=>{
  const h=harness(); const r=await h.call({action:'status'});
  assert.equal(r.zotero_online,true); assert.equal(h.saves,0);
});

test('embedded auxiliary-window focus falls back to one active web tab', async()=>{
  const h=harness({query:(q,tab)=>q.lastFocusedWindow ?
    [{id:9,active:true,url:'chrome-extension://'+'a'.repeat(32)+'/mcp-bridge.html'}] : [tab]});
  const r=await h.call({action:'status',expected_url:null});
  assert.equal(r.ok,true); assert.equal(r.selection,'unique_active_web_tab'); assert.equal(h.saves,0);
});

test('multiple active web tabs stay ambiguous and cannot save', async()=>{
  const h=harness({query:(q,tab)=>q.lastFocusedWindow ? [] :
    [tab,{id:8,active:true,url:'https://example.org/other'}]});
  const r=await h.call({action:'status',expected_url:null});
  assert.equal(r.ok,false); assert.equal(h.saves,0);
});

test('explicit exact URL selects one active article among two host windows', async()=>{
  const h=harness({query:(_q,tab)=>[tab,{id:8,active:true,url:'https://example.org/other'}]});
  const r=await h.call();
  assert.equal(r.status,'save_requested'); assert.equal(h.saves,1);
});

test('duplicate active tabs with the same exact URL cannot save', async()=>{
  const h=harness({query:(_q,tab)=>[tab,{...tab,id:8}]});
  const r=await h.call();
  assert.equal(r.ok,false); assert.equal(h.saves,0);
});
test('wrong active URL is rejected', async()=>{
  const h=harness(); const r=await h.call({expected_url:'https://example.org/other'});
  assert.equal(r.ok,false); assert.equal(h.saves,0);
});
test('webpage senders cannot invoke the bridge', async()=>{
  const h=harness(); const r=await h.call({}, {id:'other',url:'https://example.org'});
  assert.equal(r.ok,false); assert.equal(h.saves,0);
});
test('expired commands cannot save', async()=>{
  const h=harness(); const r=await h.call({expires_at:0});
  assert.equal(r.ok,false); assert.equal(h.saves,0);
});
test('internal pages cannot save', async()=>{
  const h=harness(); h.tab.url='chrome://settings'; const r=await h.call();
  assert.equal(r.ok,false); assert.equal(h.saves,0);
});

test('automatic focus chooses recent activation among host windows',async()=>{
 const h=harness({query:(q,t)=>q.lastFocusedWindow?[]:[t,{id:8,active:true,url:'https://example.org/other'}]});
 await h.activate(); const r=await h.call({action:'status',expected_url:null});
 assert.equal(r.ok,true);assert.equal(r.selection,'last_activated_web_tab');
});
test('download uses the explicit PDF link and reports completion separately',async()=>{
 const h=harness();const r=await h.call({action:'download_pdf'});
 assert.equal(r.status,'download_requested');assert.equal(h.saves,0);
 const done=await h.call({action:'download_status',download_id:42});assert.equal(done.state,'complete');
 const denied=await h.call({action:'download_status',download_id:99});assert.equal(denied.ok,false);
});
test('unreviewed cross-origin PDF destination is rejected',async()=>{
 const h=harness({links:[{label:'PDF下载',url:'https://unrelated.example/paper.pdf'}]});
 assert.equal((await h.call({action:'download_pdf'})).ok,false);
});
test('missing expiry cannot save',async()=>{
 const h=harness();assert.equal((await h.call({expires_at:undefined})).ok,false);assert.equal(h.saves,0);
});
