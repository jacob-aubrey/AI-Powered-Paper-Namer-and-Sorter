import sys

from app import App, DnDCTk
from watch_and_launch import main as watch_main


def main():
    if "--watch" in sys.argv:
        watch_main()
        return

    root = DnDCTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
