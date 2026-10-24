package tablinknet

import "testing"

func TestParseInterfaces(t *testing.T) {
	text := "wlan0 30 1500 up,broadcast,multicast 10.4.152.180/22 fe80::1/64\n" +
		"lo 1 65536 up,loopback 127.0.0.1/8\n" +
		"garbage line\n"
	got := parseInterfaces(text)
	if len(got) != 2 {
		t.Fatalf("want 2 interfaces, got %d", len(got))
	}
	w := got[0]
	if w.Name != "wlan0" || w.Index != 30 || w.MTU != 1500 || !w.IsUp() || len(w.AltAddrs) != 2 {
		t.Fatalf("bad wlan0: %+v %v", w.Interface, w.AltAddrs)
	}
	if w.AltAddrs[0].String() != "10.4.152.180/22" {
		t.Fatalf("address kept wrong: %s", w.AltAddrs[0])
	}
	if !got[1].IsLoopback() {
		t.Fatal("lo should be loopback")
	}
}
