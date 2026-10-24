package tablinknet

import (
	"net"
	"strconv"
	"strings"

	"tailscale.com/net/netmon"
)

// InterfaceSource is implemented by the Android app. Android forbids apps from listing network
// interfaces the way Go does (netlink), so the app lists them with Java's NetworkInterface and
// hands them over, one line per interface:
//
//	name index mtu flags addr/prefix addr/prefix ...
//
// flags is a comma list of: up, broadcast, loopback, pointtopoint, multicast (or "-").
type InterfaceSource interface {
	Interfaces() string
}

// SetInterfaceSource must be called before NewNode.
func SetInterfaceSource(src InterfaceSource) {
	netmon.RegisterInterfaceGetter(func() ([]netmon.Interface, error) {
		return parseInterfaces(src.Interfaces()), nil
	})
}

func parseInterfaces(text string) []netmon.Interface {
	var out []netmon.Interface
	for _, line := range strings.Split(text, "\n") {
		f := strings.Fields(line)
		if len(f) < 4 {
			continue
		}
		index, err1 := strconv.Atoi(f[1])
		mtu, err2 := strconv.Atoi(f[2])
		if err1 != nil || err2 != nil {
			continue
		}
		var flags net.Flags
		for _, fl := range strings.Split(f[3], ",") {
			switch fl {
			case "up":
				flags |= net.FlagUp | net.FlagRunning
			case "broadcast":
				flags |= net.FlagBroadcast
			case "loopback":
				flags |= net.FlagLoopback
			case "pointtopoint":
				flags |= net.FlagPointToPoint
			case "multicast":
				flags |= net.FlagMulticast
			}
		}
		addrs := []net.Addr{}
		for _, a := range f[4:] {
			if ip, ipnet, err := net.ParseCIDR(a); err == nil {
				ipnet.IP = ip
				addrs = append(addrs, ipnet)
			}
		}
		out = append(out, netmon.Interface{
			Interface: &net.Interface{Index: index, MTU: mtu, Name: f[0], Flags: flags},
			AltAddrs:  addrs,
		})
	}
	return out
}
