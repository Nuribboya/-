# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 설정.

    pyinstaller prime_finder.spec

창 하나짜리 앱이라 --onefile 로 묶는다. 실행할 때 임시 폴더에 풀리느라 첫
실행이 2~3초 느리지만, 사용자가 파일 하나만 받으면 되는 쪽이 낫다.
"""

block_cipher = None

a = Analysis(
    ['prime_contractor/gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['prime_contractor.sources.g2b', 'prime_contractor.sources.dart'],
    hookspath=[],
    runtime_hooks=[],
    # 쓰지 않는 무거운 것들을 빼서 용량을 줄인다.
    excludes=['numpy', 'pandas', 'matplotlib', 'scipy', 'PIL', 'pytest', 'sympy'],
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
