package dev.tablink

/**
 * The control-channel wire format: one JSON object per line. Kept tiny and dependency-free;
 * requests carry only a message type, never secrets.
 */
object Protocol {
    private val CON_REF = Regex("MBMID/[0-9]{1,9}")

    /** `conRef` is an aFreeRDP saved-connection reference ("MBMID/<id>") to open, if any. */
    data class Reply(
        val type: String,
        val ok: Boolean,
        val message: String,
        val conRef: String = "",
    )

    fun request(type: String): String {
        require(type.all { it.isLetterOrDigit() || it == '_' }) { "bad type: $type" }
        return "{\"type\":\"$type\"}\n"
    }

    fun parse(line: String): Reply {
        val type = stringField(line, "type") ?: "error"
        val ok = Regex("\"ok\"\\s*:\\s*(true|false)").find(line)?.groupValues?.get(1) == "true"
        val message = stringField(line, "message") ?: ""
        val conRef = stringField(line, "conRef")?.takeIf { CON_REF.matches(it) } ?: ""
        return Reply(type, ok || type == "hello", message, conRef)
    }

    private fun stringField(json: String, name: String): String? {
        val start = Regex("\"" + Regex.escape(name) + "\"\\s*:\\s*\"").find(json) ?: return null
        val out = StringBuilder()
        var i = start.range.last + 1
        while (i < json.length) {
            val c = json[i]
            when {
                c == '"' -> return out.toString()
                c == '\\' && i + 1 < json.length -> {
                    val n = json[i + 1]
                    when (n) {
                        'n' -> out.append('\n')
                        't' -> out.append('\t')
                        'u' -> if (i + 5 < json.length) {
                            out.append(json.substring(i + 2, i + 6).toInt(16).toChar())
                            i += 4
                        }
                        else -> out.append(n)
                    }
                    i += 2
                    continue
                }
                else -> out.append(c)
            }
            i++
        }
        return null
    }
}
