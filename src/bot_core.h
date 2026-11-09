#pragma once
#include <string>

inline std::string url_encode(const std::string& value) {
    static const char hex[] = "0123456789ABCDEF";
    std::string result;
    for (unsigned char c : value) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
            result += static_cast<char>(c);
        } else {
            result += '%'; result += hex[c >> 4]; result += hex[c & 15];
        }
    }
    return result;
}

inline std::string json_string(const std::string& value) {
    static const char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { result += '\\'; result += static_cast<char>(c); }
        else if (c < 0x20) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += static_cast<char>(c);
    }
    return result + '"';
}

inline std::string make_body(bool json, const std::string& user_field, const std::string& user,
                             const std::string& password_field, const std::string& password) {
    if (json) return "{" + json_string(user_field) + ":" + json_string(user) + "," +
                     json_string(password_field) + ":" + json_string(password) + "}";
    return url_encode(user_field) + "=" + url_encode(user) + "&" +
           url_encode(password_field) + "=" + url_encode(password);
}
