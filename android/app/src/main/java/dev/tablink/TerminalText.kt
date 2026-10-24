package dev.tablink

/**
 * Turns a terminal byte stream into plain text for display: drops colour/cursor escape codes,
 * honours carriage return (progress bars rewrite their line) and backspace.
 */
class TerminalText(private val maxChars: Int = 200_000) {
    private val lines = ArrayList<StringBuilder>().apply { add(StringBuilder()) }
    private var col = 0
    private var pending = ""

    fun feed(chunk: String) {
        var s = pending + chunk
        pending = ""
        // An escape sequence cut in half at the chunk end: keep it for the next chunk.
        val lastEsc = s.lastIndexOf('\u001B')
        if (lastEsc >= 0 && !COMPLETE.matches(s.substring(lastEsc)) && s.length - lastEsc < 64) {
            pending = s.substring(lastEsc)
            s = s.substring(0, lastEsc)
        }
        s = ESCAPES.replace(s, "")
        for (c in s) {
            val line = lines.last()
            when (c) {
                '\n' -> { lines.add(StringBuilder()); col = 0 }
                '\r' -> col = 0
                '\b' -> if (col > 0) col--
                '\u0007' -> Unit
                else -> {
                    if (col < line.length) line.setCharAt(col, c) else line.append(c)
                    col++
                }
            }
        }
        trim()
    }

    private fun trim() {
        var total = lines.sumOf { it.length + 1 }
        while (total > maxChars && lines.size > 1) total -= lines.removeAt(0).length + 1
    }

    override fun toString(): String = lines.joinToString("\n")

    companion object {
        private val ESCAPES = Regex("\u001B\\[[0-?]*[ -/]*[@-~]|\u001B\\][^\u0007\u001B]*(\u0007|\u001B\\\\)|\u001B[()][0-9A-Za-z]|\u001B[=>78NOMDEHc]")
        private val COMPLETE = Regex("(?s)\u001B(\\[[0-?]*[ -/]*[@-~]|\\][^\u0007\u001B]*(\u0007|\u001B\\\\)|[()][0-9A-Za-z]|[=>78NOMDEHc]).*")
    }
}
