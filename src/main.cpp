#include <algorithm>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
using Socket = SOCKET;
constexpr Socket invalid_socket = INVALID_SOCKET;
void close_socket(Socket s) { closesocket(s); }
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
using Socket = int;
constexpr Socket invalid_socket = -1;
void close_socket(Socket s) { close(s); }
#endif

namespace {
constexpr std::uint64_t max_attempts = 2000000;
const char page[] = R"HTML(<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Подбор пароля — демонстрация</title></head>
<body>
<h1>Подбор пароля</h1>
<p>Адрес этой страницы: <span id="address"></span>. Чтобы дать доступ другому человеку, используйте сетевой адрес компьютера вместо localhost.</p>
<p>Локальная демонстрация перебора: задайте свой тестовый пароль, а программа попробует его найти. Не вводите настоящие пароли. Программа не подключается к сайтам и аккаунтам.</p>
<form id="form">
<p><label>Тестовый пароль: <input name="password" type="text" maxlength="8" value="123" required autocomplete="off"></label></p>
<p><label>Символы для перебора:
<select name="alphabet"><option value="digits">Цифры 0–9</option><option value="lower">Строчные буквы a–z</option><option value="alnum">Буквы a–z и цифры 0–9</option></select></label></p>
<p><label>Максимальная длина: <input name="length" type="number" min="1" max="8" value="4" required></label></p>
<button type="submit">Начать подбор</button>
</form>
<p>Ограничение: не более 2 000 000 попыток за запуск.</p>
<pre id="result" role="status" aria-live="polite"></pre>
<script>
document.getElementById('address').textContent = window.location.href;
const form = document.getElementById('form');
const result = document.getElementById('result');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  button.disabled = true;
  result.textContent = 'Подбираем...';
  try {
    const response = await fetch('/api/guess', {method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: new URLSearchParams(new FormData(form))});
    const data = await response.json();
    if (!response.ok) result.textContent = data.error;
    else if (data.found) result.textContent = `Найдено: ${data.password}\nПопыток: ${data.attempts}\nВремя: ${data.elapsed_ms} мс`;
    else result.textContent = `Не найдено${data.limit_reached ? ' (достигнут лимит попыток)' : ''}.\nПопыток: ${data.attempts}\nВремя: ${data.elapsed_ms} мс`;
  } catch (_) { result.textContent = 'Ошибка связи с приложением.'; }
  finally { button.disabled = false; }
});
</script></body></html>)HTML";

std::string json_escape(const std::string& s) {
    std::string out;
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { out += '\\'; out += static_cast<char>(c); }
        else if (c < 0x20) { const char* hex = "0123456789abcdef"; out += "\\u00"; out += hex[c >> 4]; out += hex[c & 15]; }
        else out += static_cast<char>(c);
    }
    return out;
}

int hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
bool decode(const std::string& input, std::string& output) {
    for (size_t i = 0; i < input.size(); ++i) {
        if (input[i] == '+') output += ' ';
        else if (input[i] == '%') {
            if (i + 2 >= input.size()) return false;
            int a = hex_value(input[i + 1]), b = hex_value(input[i + 2]);
            if (a < 0 || b < 0) return false;
            output += static_cast<char>(a * 16 + b); i += 2;
        } else output += input[i];
    }
    return true;
}
bool parse_form(const std::string& body, std::map<std::string, std::string>& fields) {
    size_t pos = 0;
    while (pos <= body.size()) {
        size_t end = body.find('&', pos);
        if (end == std::string::npos) end = body.size();
        size_t eq = body.find('=', pos);
        if (eq == std::string::npos || eq >= end) return false;
        std::string key, value;
        if (!decode(body.substr(pos, eq - pos), key) || !decode(body.substr(eq + 1, end - eq - 1), value) || fields.count(key)) return false;
        fields[key] = value;
        if (end == body.size()) break;
        pos = end + 1;
    }
    return true;
}

struct Result { int status; std::string body; };
Result error(const std::string& message) { return {400, "{\"error\":\"" + json_escape(message) + "\"}"}; }
Result guess(const std::string& body) {
    std::map<std::string, std::string> fields;
    if (!parse_form(body, fields) || !fields.count("password") || !fields.count("alphabet") || !fields.count("length")) return error("Некорректные данные формы.");
    const std::string& target = fields["password"];
    const std::string& length = fields["length"];
    if (length.size() != 1 || length[0] < '1' || length[0] > '8') return error("Длина должна быть от 1 до 8.");
    int max_length = length[0] - '0';
    std::string alphabet;
    if (fields["alphabet"] == "digits") alphabet = "0123456789";
    else if (fields["alphabet"] == "lower") alphabet = "abcdefghijklmnopqrstuvwxyz";
    else if (fields["alphabet"] == "alnum") alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
    else return error("Неизвестный набор символов.");
    if (target.empty() || target.size() > static_cast<size_t>(max_length)) return error("Пароль должен быть непустым и не длиннее указанной максимальной длины.");
    if (target.find_first_not_of(alphabet) != std::string::npos) return error("Пароль содержит символы вне выбранного набора.");

    auto start = std::chrono::steady_clock::now();
    std::uint64_t attempts = 0;
    bool found = false, limit = false;
    std::string candidate;
    for (int len = 1; len <= max_length && !found && !limit; ++len) {
        std::vector<size_t> indices(static_cast<size_t>(len), 0);
        candidate.assign(static_cast<size_t>(len), alphabet[0]);
        while (true) {
            ++attempts;
            if (candidate == target) { found = true; break; }
            // Advance the rightmost character first, as in a numeric counter.
            int i = len - 1;
            for (; i >= 0; --i) {
                size_t idx = static_cast<size_t>(i);
                if (++indices[idx] < alphabet.size()) { candidate[idx] = alphabet[indices[idx]]; break; }
                indices[idx] = 0; candidate[idx] = alphabet[0];
            }
            if (i < 0) break;
            if (attempts >= max_attempts) { limit = true; break; }
        }
        if (!found && attempts >= max_attempts && len < max_length) limit = true;
    }
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
    std::ostringstream out;
    out << "{\"found\":" << (found ? "true" : "false") << ",\"password\":\"" << (found ? json_escape(candidate) : "")
        << "\",\"attempts\":" << attempts << ",\"elapsed_ms\":" << ms << ",\"limit_reached\":" << (limit ? "true" : "false") << "}";
    return {200, out.str()};
}

bool send_all(Socket socket, const std::string& data) {
    size_t sent = 0;
    while (sent < data.size()) {
#ifdef _WIN32
        int n = send(socket, data.data() + sent, static_cast<int>(data.size() - sent), 0);
#else
        ssize_t n = send(socket, data.data() + sent, data.size() - sent, MSG_NOSIGNAL);
#endif
        if (n <= 0) return false;
        sent += static_cast<size_t>(n);
    }
    return true;
}
void respond(Socket socket, int status, const std::string& content_type, const std::string& body) {
    const char* reason = status == 200 ? "OK" : status == 400 ? "Bad Request" : status == 404 ? "Not Found" : "Method Not Allowed";
    std::ostringstream header;
    header << "HTTP/1.1 " << status << ' ' << reason << "\r\nContent-Type: " << content_type << "; charset=utf-8\r\nContent-Length: " << body.size() << "\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n\r\n";
    send_all(socket, header.str() + body);
}
void handle(Socket socket) {
    // A stalled or incomplete client must not block the single-threaded demo indefinitely.
#ifdef _WIN32
    DWORD timeout = 5000;
    setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
#else
    timeval timeout{5, 0};
    setsockopt(socket, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
#endif
    std::string request;
    char buffer[4096];
    size_t header_end = std::string::npos;
    while ((header_end = request.find("\r\n\r\n")) == std::string::npos) {
        int n = static_cast<int>(recv(socket, buffer, sizeof(buffer), 0));
        if (n <= 0) return;
        request.append(buffer, static_cast<size_t>(n));
        if (request.size() > 16384) { respond(socket, 400, "text/plain", "Request too large"); return; }
    }
    std::istringstream headers(request.substr(0, header_end));
    std::string method, path, version, line;
    if (!std::getline(headers, line)) return;
    std::istringstream first(line);
    if (!(first >> method >> path >> version)) { respond(socket, 400, "text/plain", "Bad request"); return; }
    size_t content_length = 0;
    bool has_length = false, has_transfer_encoding = false;
    while (std::getline(headers, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        auto colon = line.find(':');
        if (colon == std::string::npos) continue;
        std::string name = line.substr(0, colon), value = line.substr(colon + 1);
        std::transform(name.begin(), name.end(), name.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (name == "transfer-encoding") has_transfer_encoding = true;
        if (name == "content-length") {
            if (has_length) { respond(socket, 400, "text/plain", "Bad content length"); return; }
            has_length = true;
            size_t begin = value.find_first_not_of(" \t"), end = value.find_last_not_of(" \t");
            if (begin == std::string::npos) { respond(socket, 400, "text/plain", "Bad content length"); return; }
            value = value.substr(begin, end - begin + 1);
            if (value.size() > 5 || !std::all_of(value.begin(), value.end(), [](unsigned char c) { return std::isdigit(c); })) { respond(socket, 400, "text/plain", "Bad content length"); return; }
            content_length = static_cast<size_t>(std::strtoul(value.c_str(), nullptr, 10));
            if (content_length > 4096) { respond(socket, 400, "text/plain", "Request too large"); return; }
        }
    }
    if (has_transfer_encoding) { respond(socket, 400, "text/plain", "Transfer encoding is not supported"); return; }
    if (method == "GET" && path == "/") { respond(socket, 200, "text/html", page); return; }
    if (path == "/api/guess" && method == "POST") {
        if (!has_length) { respond(socket, 400, "application/json", "{\"error\":\"Content-Length required\"}"); return; }
        size_t body_start = header_end + 4;
        while (request.size() - body_start < content_length) {
            int n = static_cast<int>(recv(socket, buffer, sizeof(buffer), 0));
            if (n <= 0) return;
            request.append(buffer, static_cast<size_t>(n));
        }
        Result result = guess(request.substr(body_start, content_length));
        respond(socket, result.status, "application/json", result.body);
    } else if (path == "/api/guess" || path == "/") respond(socket, 405, "text/plain", "Method not allowed");
    else respond(socket, 404, "text/plain", "Not found");
}
}

int main(int argc, char** argv) {
    std::string host = "127.0.0.1";
    int port = 8000;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--public") host = "0.0.0.0";
        else if (arg == "--host" && i + 1 < argc) host = argv[++i];
        else if (arg == "--port" && i + 1 < argc) {
            std::string value = argv[++i];
            if (value.empty() || value.size() > 5 || !std::all_of(value.begin(), value.end(), [](unsigned char c) { return std::isdigit(c); })) { std::cerr << "Invalid port\n"; return 1; }
            port = std::atoi(value.c_str());
            if (port < 1 || port > 65535) { std::cerr << "Invalid port\n"; return 1; }
        } else { std::cerr << "Usage: password_demo [--public | --host IPv4] [--port PORT]\n"; return 1; }
    }
#ifdef _WIN32
    WSADATA data;
    if (WSAStartup(MAKEWORD(2, 2), &data) != 0) { std::cerr << "Winsock initialization failed\n"; return 1; }
#endif
    Socket server = socket(AF_INET, SOCK_STREAM, 0);
    if (server == invalid_socket) { std::cerr << "Could not create socket\n"; return 1; }
#ifndef _WIN32
    int reuse = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
#endif
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(static_cast<unsigned short>(port));
    if (inet_pton(AF_INET, host.c_str(), &address.sin_addr) != 1) {
        std::cerr << "Invalid host: use an IPv4 address, e.g. 127.0.0.1 or 0.0.0.0\n";
        close_socket(server); return 1;
    }
    if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0 || listen(server, 16) != 0) {
        std::cerr << "Could not listen on " << host << ":" << port << ". Check the address and whether the port is in use.\n";
        close_socket(server); return 1;
    }
    std::cout << "Listening on " << host << ":" << port << std::endl;
    if (host == "0.0.0.0") {
        std::cout << "On this computer: http://localhost:" << port << "/\n"
                  << "For other devices use http://<this-computer-IP>:" << port << "/" << std::endl;
    } else {
        std::cout << "Open http://" << host << ":" << port << "/" << std::endl;
    }
    while (true) {
        Socket client = accept(server, nullptr, nullptr);
        if (client == invalid_socket) continue;
        handle(client);
        close_socket(client);
    }
}
