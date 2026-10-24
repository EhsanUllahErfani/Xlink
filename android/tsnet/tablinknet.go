// Package tablinknet puts TabLink on the user's tailnet without a separate Tailscale app or a
// VPN: a userspace Tailscale node (tsnet) inside the TabLink process.
//
// The laptop reaches the tablet over the tailnet on port 5555; this node forwards those
// connections to the tablet's own adb daemon on 127.0.0.1:5555, which is what lets the laptop
// wake and control the tablet from far away. Tailscale's keys are kept by the app (encrypted
// with Android Keystore) through the Store interface. Nothing is logged.
package tablinknet

import (
	"context"
	"errors"
	"io"
	"net"
	"os"
	"sync"
	"time"

	"tailscale.com/ipn"
	"tailscale.com/tsnet"
)

// Store is implemented by the Android app: Read returns nil when the key is missing.
type Store interface {
	Read(id string) []byte
	Write(id string, data []byte)
}

type stateStore struct{ s Store }

func (a stateStore) ReadState(id ipn.StateKey) ([]byte, error) {
	b := a.s.Read(string(id))
	if b == nil {
		return nil, ipn.ErrStateNotExist
	}
	return b, nil
}

func (a stateStore) WriteState(id ipn.StateKey, bs []byte) error {
	a.s.Write(string(id), bs)
	return nil
}

// Node is one TabLink tailnet node.
type Node struct {
	srv  *tsnet.Server
	mu   sync.Mutex
	lns  []net.Listener
	quit chan struct{}
}

func discard(string, ...any) {}

func init() {
	// Privacy: never upload Tailscale's diagnostic logs to Tailscale's servers.
	_ = os.Setenv("TS_NO_LOGS_NO_SUPPORT", "true")
}

// NewNode prepares a node; dir is private app storage for Tailscale's working files.
func NewNode(dir, hostname string, store Store) *Node {
	// Android apps have no $HOME/cache dir; without this Tailscale's log setup panics
	// ("no safe place found to store log state"). Uploads stay off (TS_NO_LOGS_NO_SUPPORT).
	_ = os.Setenv("TS_LOGS_DIR", dir)
	return &Node{
		srv: &tsnet.Server{
			Dir:      dir,
			Hostname: hostname,
			Store:    stateStore{store},
			Logf:     discard,
			UserLogf: discard,
		},
		quit: make(chan struct{}),
	}
}

// Start brings the node up in the background (returns at once).
func (n *Node) Start() error { return n.srv.Start() }

func (n *Node) status() (*ipnStatus, error) {
	lc, err := n.srv.LocalClient()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	st, err := lc.StatusWithoutPeers(ctx)
	if err != nil {
		return nil, err
	}
	out := &ipnStatus{state: st.BackendState, authURL: st.AuthURL}
	for _, ip := range st.TailscaleIPs {
		if ip.Is4() {
			out.ipv4 = ip.String()
		}
	}
	return out, nil
}

type ipnStatus struct{ state, authURL, ipv4 string }

// State is Tailscale's backend state: "NeedsLogin", "Starting", "Running", ...
func (n *Node) State() string {
	st, err := n.status()
	if err != nil {
		return "Starting"
	}
	return st.state
}

// AuthURL is the sign-in link while the node needs login ("" otherwise).
func (n *Node) AuthURL() string {
	st, err := n.status()
	if err != nil {
		return ""
	}
	if st.authURL == "" && st.state == "NeedsLogin" {
		// Ask the node to start an interactive login so a link appears.
		if lc, err := n.srv.LocalClient(); err == nil {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = lc.StartLoginInteractive(ctx)
		}
	}
	return st.authURL
}

// IPv4 is this node's tailnet address ("" until signed in).
func (n *Node) IPv4() string {
	st, err := n.status()
	if err != nil {
		return ""
	}
	return st.ipv4
}

// ForwardToLocal accepts tailnet connections on port and relays each to 127.0.0.1:port.
func (n *Node) ForwardToLocal(port int) error {
	ln, err := n.srv.Listen("tcp", ":"+itoa(port))
	if err != nil {
		return err
	}
	n.mu.Lock()
	n.lns = append(n.lns, ln)
	n.mu.Unlock()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				if errors.Is(err, net.ErrClosed) {
					return
				}
				continue
			}
			if !n.sameUser(c.RemoteAddr().String()) {
				_ = c.Close() // only the owner's own devices may reach the tablet
				continue
			}
			go relay(c, "127.0.0.1:"+itoa(port))
		}
	}()
	return nil
}

// sameUser reports whether the peer at addr belongs to the same Tailscale user as this node
// (so devices shared into the tailnet by someone else cannot reach the tablet's adb).
func (n *Node) sameUser(addr string) bool {
	lc, err := n.srv.LocalClient()
	if err != nil {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	who, err := lc.WhoIs(ctx, addr)
	if err != nil || who == nil || who.UserProfile == nil {
		return false
	}
	st, err := lc.StatusWithoutPeers(ctx)
	if err != nil || st.Self == nil {
		return false
	}
	return who.UserProfile.ID == st.Self.UserID
}

func relay(in net.Conn, target string) {
	defer in.Close()
	out, err := net.DialTimeout("tcp", target, 5*time.Second)
	if err != nil {
		return
	}
	defer out.Close()
	done := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(out, in); done <- struct{}{} }()
	go func() { _, _ = io.Copy(in, out); done <- struct{}{} }()
	<-done
}

// Logout signs this node out of the tailnet (forgets its keys).
func (n *Node) Logout() error {
	lc, err := n.srv.LocalClient()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return lc.Logout(ctx)
}

// Close stops forwarding and the node.
func (n *Node) Close() {
	n.mu.Lock()
	for _, ln := range n.lns {
		_ = ln.Close()
	}
	n.lns = nil
	n.mu.Unlock()
	_ = n.srv.Close()
}

func itoa(i int) string {
	if i == 0 {
		return "0"
	}
	var b [20]byte
	p := len(b)
	for i > 0 {
		p--
		b[p] = byte('0' + i%10)
		i /= 10
	}
	return string(b[p:])
}
