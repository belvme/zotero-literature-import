// Actual production background script and native WebSocket; browser API is mocked.
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const NativeWebSocket=require(path.join(__dirname,'../connector-build/node_modules/ws'));
class ExtensionWebSocket extends NativeWebSocket {
 constructor(url){super(process.env.TEST_BRIDGE_PORT?url.replace(':23130',':'+process.env.TEST_BRIDGE_PORT):url,{origin:'chrome-extension://'+'a'.repeat(32)});}
}
const tab={id:7,active:true,url:'https://example.org/article',title:'Article',lastAccessed:Date.now()};
const timer=(fn,ms)=>{const t=setTimeout(fn,ms);t.unref();return t;};
const interval=(fn,ms)=>{const t=setInterval(fn,ms);t.unref();return t;};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../bridge-background.js'),'utf8'),{
 WebSocket:ExtensionWebSocket,URL,console,setTimeout:timer,clearTimeout,setInterval:interval,clearInterval,
 fetch:async()=>({json:async()=>({port:23130,token:process.env.TEST_BRIDGE_TOKEN})}),
 browser:{runtime:{id:'a'.repeat(32),getURL:p=>'chrome-extension://'+'a'.repeat(32)+'/'+p,onMessage:{addListener:()=>{}}},
 tabs:{query:async()=>[tab],get:async()=>({...tab}),onActivated:{addListener:()=>{}}},
 alarms:{onAlarm:{addListener:()=>{}},create:()=>{}},
 scripting:{executeScript:async()=>[{result:[{label:'PDF下载',url:'https://example.org/paper.pdf'}]}]},
 downloads:{download:async()=>42,search:async()=>[{id:42,state:'complete',filename:'test.pdf',bytesReceived:123}]}},
 Zotero:{initDeferred:{promise:Promise.resolve()},Connector:{checkIsOnline:async()=>true},
 Connector_Browser:{getTabInfo:()=>({translators:[{label:'CNKI'}]}),saveCurrentTabFromMCP:async()=>{}}}
});
// Only the test process holds this handle. No browser page is created.
setInterval(()=>{},1000);
