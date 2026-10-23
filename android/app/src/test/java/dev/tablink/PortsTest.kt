package dev.tablink

import org.junit.Assert.assertEquals
import org.junit.Test

class PortsTest {
    @Test
    fun controlPortMatchesSpec() {
        assertEquals(47800, Ports.CONTROL)
    }
}
