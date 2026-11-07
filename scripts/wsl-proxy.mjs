// Windows-loopback bridge for WSL hosts without working localhost forwarding.
// The application itself, USB ownership and all state remain inside Compose.
import http from 'node:http';
import net from 'node:net';
const [upstream, portText, localPortText = portText] = process.argv.slice(2);
const port = Number(portText);
const localPort = Number(localPortText);
if (!net.isIPv4(upstream) || ![port,localPort].every(p=>Number.isInteger(p) && p>=1024 && p<=65535)) {
  throw new Error('Expected WSL IPv4 address and local port 8000 or 8001');
}
const server = http.createServer((request, response) => {
  const outgoing = http.request({host:upstream, port, path:request.url, method:request.method,
                                headers:request.headers, timeout:15000}, result => {
    response.writeHead(result.statusCode, result.headers);
    result.pipe(response);
  });
  outgoing.on('error', () => {
    if (!response.headersSent) response.writeHead(502, {'Content-Type':'text/plain; charset=utf-8'});
    response.end('MultiShutter in WSL nicht erreichbar. Start-WebUI.cmd erneut starten.');
  });
  outgoing.on('timeout', () => outgoing.destroy());
  request.on('aborted', () => outgoing.destroy());
  request.pipe(outgoing);
});
server.on('error', error => { console.error(`MultiShutter localhost: ${error.message}`); process.exitCode=1; });
server.listen(localPort, '127.0.0.1', () => console.log(`MultiShutter: http://localhost:${localPort}`));
