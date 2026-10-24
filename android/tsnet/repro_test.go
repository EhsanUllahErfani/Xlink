package tablinknet

import (
	"os"
	"testing"
	"time"
)

type fakeSrc struct{}

func (fakeSrc) Interfaces() string {
	return "lo 1 65536 up,loopback 127.0.0.1/8 ::1/128\n" +
		"wlan0 30 1500 up,broadcast,multicast 10.4.152.180/22 fe80::1c2d:3eff:fe4f:5a6b/64\n" +
		"rmnet_data0 12 1500 up,pointtopoint 10.0.0.9/30\n"
}

type memStore struct{ m map[string][]byte }

func (s *memStore) Read(id string) []byte        { return s.m[id] }
func (s *memStore) Write(id string, data []byte) { s.m[id] = data }

// Starts a real node with an Android-style interface list (no network login needed).
func TestNodeStartsWithInterfaceSource(t *testing.T) {
	if os.Getenv("TABLINK_REPRO") == "" {
		t.Skip("set TABLINK_REPRO=1")
	}
	SetInterfaceSource(fakeSrc{})
	n := NewNode(t.TempDir(), "tablink-repro", &memStore{m: map[string][]byte{}})
	if err := n.Start(); err != nil {
		t.Fatal(err)
	}
	if err := n.ForwardToLocal(5555); err != nil {
		t.Fatal(err)
	}
	time.Sleep(3 * time.Second)
	t.Logf("state=%s", n.State())
	n.Close()
}
