const { app, BrowserWindow, Menu, shell } = require('electron');
const path = require('path');
const fs = require('fs');

const boundsFile = () => path.join(app.getPath('userData'), 'window.json');

function readBounds() {
  try {
    const b = JSON.parse(fs.readFileSync(boundsFile(), 'utf8'));
    if (b && b.width > 320 && b.height > 320) return b;
  } catch (e) {}
  return { width: 1200, height: 880 };
}

function saveBounds(win) {
  try {
    fs.writeFileSync(boundsFile(), JSON.stringify(win.getNormalBounds()));
  } catch (e) {}
}

function buildMenu(win) {
  return Menu.buildFromTemplate([
    {
      label: '파일',
      submenu: [{ label: '닫기', accelerator: 'CmdOrCtrl+W', role: 'close' }]
    },
    {
      label: '편집',
      submenu: [
        { label: '실행 취소', role: 'undo' },
        { label: '다시 실행', role: 'redo' },
        { type: 'separator' },
        { label: '잘라내기', role: 'cut' },
        { label: '복사', role: 'copy' },
        { label: '붙여넣기', role: 'paste' },
        { label: '모두 선택', role: 'selectAll' }
      ]
    },
    {
      label: '보기',
      submenu: [
        { label: '새로 고침', accelerator: 'CmdOrCtrl+R', role: 'reload' },
        { type: 'separator' },
        { label: '확대', role: 'zoomIn' },
        { label: '축소', role: 'zoomOut' },
        { label: '기본 크기', role: 'resetZoom' },
        { type: 'separator' },
        { label: '전체 화면', role: 'togglefullscreen' },
        { label: '개발자 도구', accelerator: 'F12', role: 'toggleDevTools' }
      ]
    }
  ]);
}

function createWindow() {
  const win = new BrowserWindow({
    ...readBounds(),
    minWidth: 380,
    minHeight: 480,
    backgroundColor: '#f1f4f7',
    show: false,
    webPreferences: { contextIsolation: true, nodeIntegration: false }
  });

  Menu.setApplicationMenu(buildMenu(win));
  win.loadFile(path.join(__dirname, 'app', 'index.html'));
  win.once('ready-to-show', () => win.show());
  win.on('close', () => saveBounds(win));

  // the report has no outbound links today; if one is ever added, it opens in the browser
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  return win;
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
