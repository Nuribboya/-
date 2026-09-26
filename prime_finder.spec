# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 설정.

    pyinstaller prime_finder.spec

창 하나짜리 앱이라 --onefile 로 묶는다. 실행할 때 임시 폴더에 풀리느라 첫
실행이 2~3초 느리지만, 사용자가 파일 하나만 받으면 되는 쪽이 낫다.
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# ttkbootstrap 은 테마 정의(JSON)를 패키지 안에 데이터 파일로 들고 있다. PyInstaller가
# 파이썬 코드만 보고는 이 파일들을 안 챙기므로, 명시적으로 모아서 datas 에 넣는다.
# 안 넣으면 화면은 뜨지만 테마가 안 먹혀서 다시 기본 tkinter 룩으로 보인다.
ttkbootstrap_datas = collect_data_files('ttkbootstrap')

a = Analysis(
    ['prime_contractor/gui.py'],
    pathex=['.'],
    binaries=[],
    datas=ttkbootstrap_datas,
    hiddenimports=['prime_contractor.sources.g2b', 'prime_contractor.sources.dart'],
    hookspath=[],
    runtime_hooks=[],
    # 쓰지 않는 무거운 것들을 빼서 용량을 줄인다. PIL 은 ttkbootstrap 이 위젯을 그리는 데
    # 실제로 써서 여기서 뺐다 — 빼면 화면 자체가 안 뜬다.
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy', 'pytest', 'sympy'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PrimeFinder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # 검은 명령창 없이 GUI 만
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
