// tablink-tailnet: TabLink's built-in Tailscale node for the laptop (tsnet), so no separate
// Tailscale install is needed. The TabLink daemon runs it and talks JSON lines on stdin/stdout:
//
//	in:  {"cmd":"init","state":{"<key>":"<base64>"}}   (first line: saved state from the keyring)
//	     {"cmd":"status"} {"cmd":"login"} {"cmd":"logout"}
//	     {"cmd":"forward","listen":"127.0.0.1:15555","to":"100.x.y.z:5555"}
//	out: {"type":"status",...} {"type":"forward","ok":true} {"type":"error","message":...}
//	     {"type":"state","key":"<key>","data":"<base64>"} whenever Tailscale saves state;
//	     the daemon keeps it in the GNOME keyring. Nothing is written to disk but working files.
package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"

	"tailscale.com/ipn"
	"tailscale.com/tsnet"
)

var (
	outMu sync.Mutex
	enc   = json.NewEncoder(os.Stdout)
)

func send(v any) {
	outMu.Lock()
	defer outMu.Unlock()
	_ = enc.Encode(v)
}

type store struct {
	mu sync.Mutex
	m  map[string][]byte
}

func (s *store) ReadState(id ipn.StateKey) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	b, ok := s.m[string(id)]
	if !ok {
		return nil, ipn.ErrStateNotExist
	}
	return b, nil
}

func (s *store) WriteState(id ipn.StateKey, bs []byte) error {
	s.mu.Lock()
	s.m[string(id)] = append([]byte(nil), bs...)
	s.mu.Unlock()
	send(map[string]string{"type": "state", "key": string(id), "data": base64.StdEncoding.EncodeToString(bs)})
	return nil
}

type peer struct {
	Host   string   `json:"host"`
	OS     string   `json:"os"`
	IPs    []string `json:"ips"`
	Online bool     `json:"online"`
}

func discard(string, ...any) {}

func init() {
	// Privacy: never upload Tailscale's diagnostic logs to Tailscale's servers.
	_ = os.Setenv("TS_NO_LOGS_NO_SUPPORT", "true")
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 16<<20)
	if !in.Scan() {
		return
	}
	var init struct {
		Cmd   string            `json:"cmd"`
		State map[string]string `json:"state"`
		Dir   string            `json:"dir"`
		Host  string            `json:"hostname"`
	}
	if err := json.Unmarshal(in.Bytes(), &init); err != nil || init.Cmd != "init" {
		send(map[string]string{"type": "error", "message": "first line must be init"})
		return
	}
	st := &store{m: map[string][]byte{}}
	for k, v := range init.State {
		if b, err := base64.StdEncoding.DecodeString(v); err == nil {
			st.m[k] = b
		}
	}
	dir := init.Dir
	if dir == "" {
		dir = filepath.Join(os.TempDir(), "tablink-tailnet")
	}
	_ = os.MkdirAll(dir, 0o700)
	_ = os.Setenv("TS_LOGS_DIR", dir) // keep Tailscale's local log state with its other files
	host := init.Host
	if host == "" {
		host = "tablink-laptop"
	}
	srv := &tsnet.Server{Dir: dir, Hostname: host, Store: st, Logf: discard, UserLogf: discard}
	if err := srv.Start(); err != nil {
		send(map[string]string{"type": "error", "message": err.Error()})
		return
	}
	defer srv.Close()
	lc, _ := srv.LocalClient()
	forwards := map[string]net.Listener{}

	for in.Scan() {
		var req struct {
			Cmd    string `json:"cmd"`
			Listen string `json:"listen"`
			To     string `json:"to"`
		}
		if json.Unmarshal(in.Bytes(), &req) != nil {
			send(map[string]string{"type": "error", "message": "bad request"})
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		switch req.Cmd {
		case "status":
			s, err := lc.Status(ctx)
			if err != nil {
				send(map[string]any{"type": "status", "state": "Starting"})
				break
			}
			out := map[string]any{"type": "status", "state": s.BackendState, "auth_url": s.AuthURL}
			var ips []string
			if s.Self != nil {
				for _, ip := range s.Self.TailscaleIPs {
					ips = append(ips, ip.String())
				}
			}
			out["ips"] = ips
			var peers []peer
			for _, p := range s.Peer {
				pp := peer{Host: p.HostName, OS: p.OS, Online: p.Online}
				for _, ip := range p.TailscaleIPs {
					pp.IPs = append(pp.IPs, ip.String())
				}
				peers = append(peers, pp)
			}
			out["peers"] = peers
			send(out)
		case "login":
			err := lc.StartLoginInteractive(ctx)
			send(map[string]any{"type": "login", "ok": err == nil})
		case "logout":
			err := lc.Logout(ctx)
			send(map[string]any{"type": "logout", "ok": err == nil})
		case "forward":
			if _, ok := forwards[req.Listen]; ok {
				send(map[string]any{"type": "forward", "ok": true})
				break
			}
			ln, err := net.Listen("tcp", req.Listen)
			if err != nil {
				send(map[string]any{"type": "forward", "ok": false, "message": err.Error()})
				break
			}
			forwards[req.Listen] = ln
			go serve(srv, ln, req.To)
			send(map[string]any{"type": "forward", "ok": true})
		default:
			send(map[string]string{"type": "error", "message": "unknown command"})
		}
		cancel()
	}
}

// serve relays local connections (adb on this laptop) to `to` over the tailnet.
func serve(srv *tsnet.Server, ln net.Listener, to string) {
	for {
		c, err := ln.Accept()
		if err != nil {
			return
		}
		go func(in net.Conn) {
			defer in.Close()
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			out, err := srv.Dial(ctx, "tcp", to)
			cancel()
			if err != nil {
				return
			}
			defer out.Close()
			done := make(chan struct{}, 2)
			go func() { _, _ = io.Copy(out, in); done <- struct{}{} }()
			go func() { _, _ = io.Copy(in, out); done <- struct{}{} }()
			<-done
		}(c)
	}
}
