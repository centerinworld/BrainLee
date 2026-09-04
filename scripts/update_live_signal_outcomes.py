#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from live_signal_tracker import update_outcomes
print({'updated':update_outcomes()})
