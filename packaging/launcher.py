"""Entry point for the packaged desktop app."""

import multiprocessing
import sys

if __name__ == "__main__":
    # Libraries used for captions start Python helper processes by re-running this app.
    # freeze_support() recognises those launches and runs the helper instead of the app.
    multiprocessing.freeze_support()
    from longform.desktop import main

    sys.exit(main())
