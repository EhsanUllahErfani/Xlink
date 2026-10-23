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
        val mode: String = "",
    )

    /** Human label for the laptop's active link mode. */
    fun modeLabel(mode: String): String = when (mode) {
        "usb" -> "USB cable"
        "lan" -> "Wi-Fi"
        "laptop_hotspot" -> "laptop hotspot"
        "tablet_hotspot" -> "tablet hotspot"
        "tailscale" -> "internet (Tailscale)"
        "bluetooth" -> "Bluetooth"
        else -> "connected"
    }

    fun request(type: String): String {
        require(type.all { it.isLetterOrDigit() || it == '_' }) { "bad type: $type" }
        return "{\"type\":\"$type\"}\n"
    }

    fun auth(mac: String): String {
        require(mac.all { it.isLetterOrDigit() }) { "bad mac" }
        return "{\"type\":\"auth\",\"mac\":\"$mac\"}\n"
    }

    /** JSON string literal with escapes (for clipboard and notification text). */
    fun quote(text: String): String {
        val sb = StringBuilder("\"")
        for (c in text) {
            when {
                c == '"' -> sb.append("\\\"")
                c == '\\' -> sb.append("\\\\")
                c == '\n' -> sb.append("\\n")
                c == '\r' -> sb.append("\\r")
                c == '\t' -> sb.append("\\t")
                c < ' ' || c == '\u2028' || c == '\u2029' -> sb.append("\\u%04x".format(c.code))
                else -> sb.append(c)
            }
        }
        return sb.append('"').toString()
    }

    fun clip(text: String): String = "{\"type\":\"clip\",\"text\":${quote(text)}}\n"

    fun notification(app: String, title: String, text: String): String =
        "{\"type\":\"notification\",\"app\":${quote(app)},\"title\":${quote(title)}," +
            "\"text\":${quote(text)}}\n"

    fun fileHeader(name: String, size: Long): String =
        "{\"type\":\"file\",\"name\":${quote(name)},\"size\":$size}\n"

    fun parse(line: String): Reply {
        val type = stringField(line, "type") ?: "error"
        val ok = Regex("\"ok\"\\s*:\\s*(true|false)").find(line)?.groupValues?.get(1) == "true"
        val message = stringField(line, "message") ?: ""
        val conRef = stringField(line, "conRef")?.takeIf { CON_REF.matches(it) } ?: ""
        val mode = stringField(line, "mode") ?: ""
        if (type == "clip") return Reply(type, true, stringField(line, "text") ?: "", "", "")
        return Reply(type, ok || type == "hello", message, conRef, mode)
    }

    fun stringField(json: String, name: String): String? {
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
                        'r' -> out.append('\r')
                        'b' -> out.append('\b')
                        'f' -> out.append('\u000c')
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
