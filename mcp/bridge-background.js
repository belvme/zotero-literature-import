/* Runs INSIDE a user-installed, modified Zotero Connector, not inside a webpage. */
(() => {
 const pageURL=browser.runtime.getURL('mcp-bridge.html'), web=t=>/^https?:\/\//.test(t?.url||'');
 let lastWeb;
 const downloads=new Set();
 browser.tabs.onActivated?.addListener(async({tabId})=>{try{if(web(await browser.tabs.get(tabId)))lastWeb=tabId;}catch(_){}});
 async function execute(m){
  try{
   await Zotero.initDeferred.promise;
   if(!Number.isFinite(m.expires_at)||Date.now()>=m.expires_at*1000)throw Error('Command expired');
   if(m.action==='download_status'){
    if(!downloads.has(m.download_id))throw Error('Download not initiated by this bridge session');
    const [d]=await browser.downloads.search({id:m.download_id});
    if(!d)throw Error('Download no longer exists');
    return {ok:true,download_id:d.id,state:d.state,error:d.error,filename:d.state==='complete'?d.filename:undefined,bytes:d.bytesReceived};
   }
   if(m.expected_url&&!/^https?:\/\//.test(m.expected_url))throw Error('Expected URL must be HTTP(S)');
   let tabs=await browser.tabs.query({active:true,lastFocusedWindow:true}),selection='last_focused_window';
   if(m.expected_url){tabs=(await browser.tabs.query({active:true})).filter(t=>web(t)&&t.url===m.expected_url);selection='exact_url_active_web_tab';}
   else if(tabs.length!==1||!web(tabs[0])){
    tabs=(await browser.tabs.query({active:true})).filter(web);selection='unique_active_web_tab';
    if(tabs.length>1){
     const active=tabs.filter(t=>t.id===lastWeb),recent=[...tabs].sort((a,b)=>(b.lastAccessed||0)-(a.lastAccessed||0));
     if(active.length===1){tabs=active;selection='last_activated_web_tab';}
     else if(recent[0].lastAccessed>(recent[1].lastAccessed||0)){tabs=[recent[0]];selection='most_recent_active_web_tab';}
    }
   }
   if(tabs.length!==1||!web(tabs[0]))throw Error('Cannot select an unambiguous active HTTP(S) article tab ('+tabs.length+' candidates). Nothing saved.');
   const tab=tabs[0],info=Zotero.Connector_Browser.getTabInfo(tab.id);
   if(m.action==='status')return {ok:true,bridge_version:2,url:tab.url,title:tab.title,tab_id:tab.id,selection,translators:(info.translators||[]).map(t=>t.label),zotero_online:await Zotero.Connector.checkIsOnline()};
   if(!['save','download_pdf'].includes(m.action))throw Error('Unknown command');
   const current=await browser.tabs.get(tab.id);
   if(current.url!==tab.url||!current.active)throw Error('Page changed');
   if(m.action==='download_pdf'){
    if(!m.expected_url)throw Error('PDF download requires explicit expected URL');
    const results=await browser.scripting.executeScript({target:{tabId:tab.id},func:()=>[...document.querySelectorAll('a[href]')].filter(a=>a.getClientRects().length&&(/^PDF\s*下载$/i.test(a.textContent.trim())||/\.pdf(?:[?#]|$)/i.test(a.href))).map(a=>({url:a.href,label:a.textContent.trim()}))});
    const links=[...new Map((results[0]?.result||[]).map(x=>[x.url,x])).values()],primary=links.filter(x=>/^PDF\s*下载$/i.test(x.label)),choices=primary.length?primary:links;
    if(choices.length!==1)throw Error('No unique explicit PDF download link found');
    const url=new URL(choices[0].url),source=new URL(current.url);
    if(!['http:','https:'].includes(url.protocol)||url.username||url.password||!(url.origin===source.origin||(source.hostname.endsWith('.cnki.net')&&url.hostname.endsWith('.cnki.net'))))throw Error('PDF link requires separate destination review');
    const recheck=await browser.tabs.get(tab.id);if(recheck.url!==current.url||!recheck.active)throw Error('Page changed');
    const id=await browser.downloads.download({url:url.href,saveAs:false});downloads.add(id);
    return {ok:true,status:'download_requested',download_id:id,url:current.url,warning:'Verify completed file bytes and PDF before import.'};
   }
   if(!(info.translators||[]).length)throw Error('No literature translator detected yet; nothing saved');
   await Zotero.Connector_Browser.saveCurrentTabFromMCP(current);
   return {ok:true,status:'save_requested',url:current.url,warning:'Verify actual Zotero item and PDF; action request is not completion.'};
  }catch(e){return {ok:false,error:String(e.message||e)};}
 }
 browser.runtime.onMessage.addListener((m,sender)=>{
  if(m?.type!=='zoteroMcpBridge')return;
  if(sender.id!==browser.runtime.id||sender.url!==pageURL)return Promise.resolve({ok:false,error:'Untrusted bridge sender'});
  return execute(m);
 });
 if(typeof WebSocket==='undefined')return;
 let socket,reconnectTimer,heartbeat,connecting=false,chain=Promise.resolve();
 const executed=new Set();
 async function connect(){
  if(connecting||socket?.readyState===WebSocket.OPEN||socket?.readyState===WebSocket.CONNECTING)return;
  connecting=true;
  try{
   const config=await(await fetch(browser.runtime.getURL('mcp-private-config.json'))).json();
   if(typeof config.token!=='string'||config.token.length<32||config.port!==23130)throw Error('Private bridge config invalid');
   const ws=socket=new WebSocket('ws://127.0.0.1:'+config.port);
   ws.onopen=()=>{ws.send(JSON.stringify({token:config.token}));heartbeat=setInterval(()=>{if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'heartbeat'}));},20000);};
   ws.onmessage=event=>{
    let data;try{data=JSON.parse(event.data);}catch(_){return;}
    if(data.type!=='job')return;
    const job=data.job;
    chain=chain.then(async()=>{
     let result;if(executed.has(job.id))result={ok:false,error:'Duplicate command refused'};
     else{executed.add(job.id);if(executed.size>256)executed.delete(executed.values().next().value);result=await execute(job);}
     if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({type:'result',id:job.id,result}));
    }).catch(()=>{});
   };
   ws.onerror=()=>ws.close();
   ws.onclose=()=>{clearInterval(heartbeat);if(socket===ws)socket=null;clearTimeout(reconnectTimer);reconnectTimer=setTimeout(connect,3000);};
  }catch(_){clearTimeout(reconnectTimer);reconnectTimer=setTimeout(connect,3000);}
  finally{connecting=false;}
 }
 browser.alarms?.onAlarm.addListener(a=>{if(a.name==='zotero-mcp-reconnect')connect();});
 browser.alarms?.create('zotero-mcp-reconnect',{periodInMinutes:1});
 connect();
})();
