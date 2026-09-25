import sys

# spaCy's thinc imports PyTorch when it happens to be installed, which costs ~150 MB of RAM and several seconds of startup.
# This app only uses the small CPU model (numpy/blis), so keep torch out unless something has already loaded it.
sys.modules.setdefault("torch", None)  # type: ignore[arg-type]
