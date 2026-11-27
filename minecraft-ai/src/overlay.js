import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

/** Serves overlay/index.html (OBS browser source) + /events Server-Sent Events. */
export function startOverlay(events, port) {
  const html = () => fs.readFileSync(path.join(here, '..', 'overlay', 'index.html'));
  const clients = new Set();

  const server = http.createServer((req, res) => {
    if (req.url === '/events') {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
      res.write(`data: ${JSON.stringify(events.state)}\n\n`);
      clients.add(res);
      req.on('close', () => clients.delete(res));
      return;
    }
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(html());
  });

  events.on('update', (state) => {
    const data = `data: ${JSON.stringify(state)}\n\n`;
    for (const c of clients) c.write(data);
  });

  server.listen(port, () => console.log(`오버레이: http://localhost:${port}  (OBS 브라우저 소스로 추가)`));
  return server;
}
