package dev.tablink

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test
    fun requestIsOneJsonLine() {
        assertEquals("{\"type\":\"remote_ready\"}\n", Protocol.request("remote_ready"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun requestRejectsInjection() {
        Protocol.request("x\",\"evil\":\"1")
    }

    @Test
    fun parsesLaptopReplies() {
        val r = Protocol.parse(
            "{\"type\": \"remote\", \"ok\": true, \"message\": \"The laptop desktop is opening on the tablet.\"}"
        )
        assertEquals("remote", r.type)
        assertTrue(r.ok)
        assertEquals("The laptop desktop is opening on the tablet.", r.message)
    }

    @Test
    fun parsesEscapesAndFailures() {
        val r = Protocol.parse("{\"type\": \"screen\", \"ok\": false, \"message\": \"Say \\\"hi\\\" \\u00e9\"}")
        assertFalse(r.ok)
        assertEquals("Say \"hi\" é", r.message)
    }

    @Test
    fun helloCountsAsOk() {
        val r = Protocol.parse("{\"type\": \"hello\", \"app\": \"tablink-laptop\", \"version\": \"0.1.0\"}")
        assertTrue(r.ok)
    }

    @Test
    fun garbageIsAnError() {
        assertEquals("error", Protocol.parse("nonsense").type)
    }

    @Test
    fun readsSavedConnectionReference() {
        val r = Protocol.parse("{\"type\": \"remote\", \"ok\": true, \"message\": \"x\", \"conRef\": \"MBMID/1\"}")
        assertEquals("MBMID/1", r.conRef)
    }

    @Test
    fun ignoresUnexpectedReferences() {
        val r = Protocol.parse("{\"type\": \"remote\", \"ok\": true, \"conRef\": \"FILE//sdcard/evil.rdp\"}")
        assertEquals("", r.conRef)
    }

    @Test
    fun helloCarriesLinkMode() {
        val r = Protocol.parse("{\"type\": \"hello\", \"mode\": \"lan\"}")
        assertEquals("lan", r.mode)
        assertEquals("Wi-Fi", Protocol.modeLabel(r.mode))
    }

    @Test
    fun clipRoundTripsAwkwardText() {
        val text = "line1\nline2\t\"quoted\" \\ back é 😀 \u0001"
        val r = Protocol.parse(Protocol.clip(text).trim())
        assertEquals("clip", r.type)
        assertEquals(text, r.message)
    }

    @Test
    fun hmacMatchesLaptop() {
        // Python: hmac.new(b"secret", b"abc", hashlib.sha256).hexdigest()
        assertEquals(
            "9946dad4e00e913fc8be8e5d3f7e110a4a9e832f83fb09c345285d78638d8a0e",
            ControlClient.hmac("secret", "abc"),
        )
    }

    @Test
    fun fileHeaderEscapesName() {
        assertEquals(
            "{\"type\":\"file\",\"name\":\"a\\\"b.jpg\",\"size\":10}\n",
            Protocol.fileHeader("a\"b.jpg", 10),
        )
    }

    @Test
    fun helloCarriesWakeDetails() {
        val r = Protocol.parse(
            "{\"type\": \"hello\", \"mode\": \"lan\", \"wake_mac\": \"60:ff:9e:01:02:03\", " +
                "\"wake_ip\": \"10.4.151.217\"}",
        )
        assertEquals("60:ff:9e:01:02:03", r.wakeMac)
        assertEquals("10.4.151.217", r.wakeIp)
        val bad = Protocol.parse("{\"type\": \"hello\", \"wake_mac\": \"zz\", \"wake_ip\": \"x;rm\"}")
        assertEquals("", bad.wakeMac)
        assertEquals("", bad.wakeIp)
    }

    @Test
    fun magicPacketIsStandard() {
        val p = WakeLaptop.magicPacket("60:ff:9e:01:02:03")
        assertEquals(102, p.size)
        assertTrue(p.take(6).all { it == 0xFF.toByte() })
        assertEquals(0x60.toByte(), p[6])
        assertEquals(0x03.toByte(), p[101])
    }
}
