//go:build tools

// Keeps gomobile's bind package pinned in go.mod (gomobile bind needs it).
package tablinknet

import _ "golang.org/x/mobile/bind"
