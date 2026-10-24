package dev.tablink

import org.junit.Assert.assertEquals
import org.junit.Test

class TerminalTextTest {
    @Test
    fun stripsColoursAndPrompts() {
        val t = TerminalText()
        t.feed("\u001B[01;32malex@laptop\u001B[00m:\u001B[01;34m~\u001B[00m$ ls\r\nfile.txt\r\n")
        assertEquals("alex@laptop:~$ ls\nfile.txt\n", t.toString())
    }

    @Test
    fun progressBarRewritesItsLine() {
        val t = TerminalText()
        t.feed("epoch 1: 10%\repoch 1: 55%\repoch 1: 100%\n")
        assertEquals("epoch 1: 100%\n", t.toString())
    }

    @Test
    fun escapeSplitAcrossChunks() {
        val t = TerminalText()
        t.feed("ok \u001B[3")
        t.feed("1mred\u001B[0m done")
        assertEquals("ok red done", t.toString())
    }

    @Test
    fun windowTitleCodesAreDropped() {
        val t = TerminalText()
        t.feed("\u001B]0;alex@laptop: ~\u0007$ ")
        assertEquals("$ ", t.toString())
    }

    @Test
    fun keepsOnlyTheLastPartOfLongOutput() {
        val t = TerminalText(maxChars = 50)
        repeat(100) { t.feed("line $it\n") }
        val text = t.toString()
        assert(text.length <= 60)
        assert(text.contains("line 99"))
    }
}
