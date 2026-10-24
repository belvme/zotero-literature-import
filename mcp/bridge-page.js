const statusNode = document.getElementById('status');
const button = document.getElementById('connect');
let running = false;
const client = crypto.randomUUID().replaceAll('-', '');
const executed = new Set();
button.addEventListener('click', async () => {
  if (running) { running = false; button.textContent = 'Connect'; return; }
  const token = document.getElementById('token').value.trim();
  const port = Number(document.getElementById('port').value);
  if (token.length < 32 || !Number.isInteger(port) || port < 1024 || port > 65535) {
    statusNode.textContent = 'Enter the bridge token and a valid port.'; return;
  }
  const base = `http://127.0.0.1:${port}`;
  const headers = {'X-Zotero-MCP-Token': token, 'X-Zotero-MCP-Client': client};
  running = true;
  button.textContent = 'Disconnect';
  while (running) {
    try {
      const response = await fetch(base + '/next', {headers, signal: AbortSignal.timeout(5000)});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || String(response.status));
      statusNode.textContent = 'Connected. Keep this tab open and focus the article tab.';
      if (data.job) {
        const job = data.job;
        let result;
        if (executed.has(job.id)) result = {ok: false, error: 'Duplicate command refused'};
        else {
          executed.add(job.id);
          try { result = await browser.runtime.sendMessage({type: 'zoteroMcpBridge', ...job}); }
          catch (error) { result = {ok: false, error: String(error.message || error)}; }
        }
        const ack = await fetch(base + '/result', {method: 'POST',
          headers: {...headers, 'Content-Type': 'application/json'},
          body: JSON.stringify({id: job.id, result}), signal: AbortSignal.timeout(5000)});
        if (!ack.ok) throw new Error('Result not accepted. Inspect Zotero before retrying.');
      }
    } catch (error) {
      statusNode.textContent = String(error.message || error);
      running = false; button.textContent = 'Connect'; break;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
});
