"""Acquire the controlling terminal in a separate process before starting the shell."""
import fcntl
import os
import sys
import termios

fcntl.ioctl(0, termios.TIOCSCTTY, 0)
shell = sys.argv[1]
os.execv(shell, [shell, '-i'])
