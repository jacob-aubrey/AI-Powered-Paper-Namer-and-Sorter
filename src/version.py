"""Release version shared by the window title and Windows executable metadata."""

APP_NAME = "AI Paper Sorter"
APP_VERSION = "1.3.1"
WINDOW_TITLE = f"{APP_NAME} v{APP_VERSION}"
VERSION_TUPLE = tuple(int(part) for part in APP_VERSION.split(".")) + (0,)


def windows_version_info() -> str:
    """Render PyInstaller's version resource from the same displayed version."""
    return f"""# UTF-8
# Generated from src/version.py; edit APP_VERSION there.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION_TUPLE},
    prodvers={VERSION_TUPLE},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [
          StringStruct(u'CompanyName', u''),
          StringStruct(u'FileDescription', u'{APP_NAME}'),
          StringStruct(u'FileVersion', u'{APP_VERSION}'),
          StringStruct(u'InternalName', u'{APP_NAME}'),
          StringStruct(u'OriginalFilename', u'{APP_NAME}.exe'),
          StringStruct(u'ProductName', u'{APP_NAME}'),
          StringStruct(u'ProductVersion', u'{APP_VERSION}'),
        ]
      )
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
