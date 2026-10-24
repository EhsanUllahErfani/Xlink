package tablinknet

import (
	"os"
	"runtime/debug"
)

// SetCrashFile makes a fatal Go error write its report to path (inside the app's private
// storage), so a crash of the built-in Tailscale can be diagnosed afterwards.
func SetCrashFile(path string) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	return debug.SetCrashOutput(f, debug.CrashOptions{})
}
