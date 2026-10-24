package tablinknet

import (
	"context"
	"io"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Router carries the tablet app's local connections (127.0.0.1:port) to the laptop through
// whichever node reaches it: TabLink's own network first, then Tailscale. The app keeps
// dialling 127.0.0.1 and never needs USB or an adb tunnel.
type Router struct {
	mu    sync.Mutex
	nodes []*Node
	lns   []net.Listener
}

func NewRouter() *Router { return &Router{} }

// Add appends a node; earlier nodes are preferred.
func (r *Router) Add(n *Node) {
	r.mu.Lock()
	r.nodes = append(r.nodes, n)
	r.mu.Unlock()
}

// Reset forgets the nodes (they are added again, in order of preference).
func (r *Router) Reset() {
	r.mu.Lock()
	r.nodes = nil
	r.mu.Unlock()
}

// laptopAddr finds the laptop's TabLink node ("tablink-...", not Android) on node n.
func laptopAddr(n *Node) string {
	lc, err := n.srv.LocalClient()
	if err != nil {
		return ""
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	st, err := lc.Status(ctx)
	if err != nil {
		return ""
	}
	for _, p := range st.Peer {
		if !p.Online || !strings.HasPrefix(p.HostName, "tablink-") || strings.EqualFold(p.OS, "android") {
			continue
		}
		for _, ip := range p.TailscaleIPs {
			if ip.Is4() {
				return ip.String()
			}
		}
	}
	return ""
}

// LaptopVia names the network that currently reaches the laptop ("" if none):
// the index of the node, as "0", "1", ...
func (r *Router) LaptopVia() string {
	r.mu.Lock()
	nodes := append([]*Node(nil), r.nodes...)
	r.mu.Unlock()
	for i, n := range nodes {
		if laptopAddr(n) != "" {
			return strconv.Itoa(i)
		}
	}
	return ""
}

// ListenLocal accepts on 127.0.0.1:localPort and relays to the laptop's peerPort.
func (r *Router) ListenLocal(localPort, peerPort int) error {
	ln, err := net.Listen("tcp", "127.0.0.1:"+strconv.Itoa(localPort))
	if err != nil {
		return err
	}
	r.mu.Lock()
	r.lns = append(r.lns, ln)
	r.mu.Unlock()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go r.relay(c, peerPort)
		}
	}()
	return nil
}

func (r *Router) relay(in net.Conn, peerPort int) {
	defer in.Close()
	r.mu.Lock()
	nodes := append([]*Node(nil), r.nodes...)
	r.mu.Unlock()
	for _, n := range nodes {
		addr := laptopAddr(n)
		if addr == "" {
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		out, err := n.srv.Dial(ctx, "tcp", net.JoinHostPort(addr, strconv.Itoa(peerPort)))
		cancel()
		if err != nil {
			continue
		}
		defer out.Close()
		done := make(chan struct{}, 2)
		go func() { _, _ = io.Copy(out, in); done <- struct{}{} }()
		go func() { _, _ = io.Copy(in, out); done <- struct{}{} }()
		<-done
		return
	}
}

// Close stops listening.
func (r *Router) Close() {
	r.mu.Lock()
	for _, ln := range r.lns {
		_ = ln.Close()
	}
	r.lns = nil
	r.mu.Unlock()
}
