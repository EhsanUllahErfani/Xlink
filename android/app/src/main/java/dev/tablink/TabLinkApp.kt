package dev.tablink

import com.freerdp.freerdpcore.application.GlobalApp

/**
 * TabLink's application. It extends FreeRDP's GlobalApp, which owns the remote-desktop
 * sessions (the RDP engine built into this app; see android/scripts/build-freerdp.sh).
 */
class TabLinkApp : GlobalApp()
