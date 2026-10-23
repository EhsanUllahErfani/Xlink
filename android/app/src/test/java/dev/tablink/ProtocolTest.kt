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
}
