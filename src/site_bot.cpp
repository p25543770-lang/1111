// Windows console tool for low-volume, authorized login-protection checks.
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>
#include <conio.h>
#include <chrono>
#include <iostream>
#include <string>
#include <thread>
#include "bot_core.h"

struct Handle {
    HINTERNET value = nullptr;
    explicit Handle(HINTERNET h = nullptr) : value(h) {}
    ~Handle() { if (value) WinHttpCloseHandle(value); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
};

std::wstring widen(const std::string& text) {
    if (text.empty()) return {};
    int size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (!size) return {};
    std::wstring result(static_cast<size_t>(size), L'\0');
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), &result[0], size)) return {};
    return result;
}
std::string ask(const char* prompt, const char* fallback = "") {
    std::cout << prompt << std::flush;
    std::string s;
    std::getline(std::cin, s);
    return s.empty() ? fallback : s;
}
std::string secret() {
    std::cout << "Incorrect TEST password (hidden; never use a real one): " << std::flush;
    std::string out;
    while (true) {
        int c = _getch();
        if (c == '\r' || c == '\n') break;
        if (c == 3 || c == 27) { out.clear(); break; }
        if (c == 8) { if (!out.empty()) out.pop_back(); }
        else if (c == 0 || c == 224) { (void)_getch(); }
        else if (c >= 32 && c <= 126 && out.size() < 256) out += static_cast<char>(c);
    }
    std::cout << "\n";
    return out;
}
int main() {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);
    std::cout << "Login protection checker (Windows)\n"
                 "Only test a site you own or have explicit permission to test.\n"
                 "Uses one test account and one deliberately incorrect password, up to 5 requests.\n"
                 "It does not guess passwords. Stops on HTTP 403 or 429.\n\n";
    std::string url = ask("HTTPS URL of the login POST endpoint: ");
    std::wstring wide_url = widen(url);
    URL_COMPONENTS parts{};
    parts.dwStructSize = sizeof(parts);
    parts.dwSchemeLength = parts.dwHostNameLength = parts.dwUrlPathLength = parts.dwExtraInfoLength = static_cast<DWORD>(-1);
    parts.dwUserNameLength = parts.dwPasswordLength = static_cast<DWORD>(-1);
    if (wide_url.empty() || !WinHttpCrackUrl(wide_url.c_str(), 0, 0, &parts) ||
        parts.nScheme != INTERNET_SCHEME_HTTPS || parts.dwHostNameLength == 0 ||
        parts.dwUserNameLength != 0 || parts.dwPasswordLength != 0 ||
        url.find('#') != std::string::npos) {
        std::cerr << "Invalid URL: use an HTTPS endpoint without credentials or a fragment.\n";
        return 1;
    }
    std::wstring host(parts.lpszHostName, parts.dwHostNameLength);
    std::wstring path;
    if (parts.dwUrlPathLength) path.assign(parts.lpszUrlPath, parts.dwUrlPathLength);
    if (path.empty()) path = L"/";
    if (parts.dwExtraInfoLength) path.append(parts.lpszExtraInfo, parts.dwExtraInfoLength);
    std::string username_field = ask("Username field [username]: ", "username");
    std::string username = ask("Test account username: ");
    std::string password_field = ask("Password field [password]: ", "password");
    std::string password = secret();
    std::string format = ask("Body format: 1=form, 2=JSON [1]: ", "1");
    std::string count = ask("Number of requests, 1-5 [3]: ", "3");
    if (username.empty() || password.empty() || username_field == password_field ||
        (format != "1" && format != "2") || count.size() != 1 || count[0] < '1' || count[0] > '5') {
        std::cerr << "Invalid input. Enter non-empty credentials, distinct field names and a valid format/count.\n";
        return 1;
    }
    if (ask("Type YES to confirm authorization and a disposable test account: ") != "YES") {
        std::cout << "Cancelled.\n"; return 0;
    }
    bool json = format == "2";
    std::string body = make_body(json, username_field, username, password_field, password);
    Handle session(WinHttpOpen(L"SiteProtectionChecker/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY,
                               WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0));
    if (!session.value) { std::cerr << "WinHttpOpen error: " << GetLastError() << "\n"; return 1; }
    WinHttpSetTimeouts(session.value, 10000, 10000, 10000, 10000);
    Handle connection(WinHttpConnect(session.value, host.c_str(), parts.nPort, 0));
    if (!connection.value) { std::cerr << "Connection error: " << GetLastError() << "\n"; return 1; }
    for (int i = 1; i <= count[0] - '0'; ++i) {
        Handle request(WinHttpOpenRequest(connection.value, L"POST", path.c_str(), nullptr, WINHTTP_NO_REFERER,
                                          WINHTTP_DEFAULT_ACCEPT_TYPES, WINHTTP_FLAG_SECURE));
        if (!request.value) { std::cerr << "Request error: " << GetLastError() << "\n"; return 1; }
        DWORD disable = WINHTTP_DISABLE_REDIRECTS | WINHTTP_DISABLE_COOKIES;
        if (!WinHttpSetOption(request.value, WINHTTP_OPTION_DISABLE_FEATURE, &disable, sizeof(disable))) {
            std::cerr << "Cannot disable redirects and cookies: " << GetLastError() << "\n"; return 1;
        }
        const wchar_t* headers = json ? L"Content-Type: application/json; charset=utf-8\r\n" :
                                         L"Content-Type: application/x-www-form-urlencoded\r\n";
        if (!WinHttpSendRequest(request.value, headers, static_cast<DWORD>(-1L),
                                const_cast<char*>(body.data()), static_cast<DWORD>(body.size()),
                                static_cast<DWORD>(body.size()), 0) || !WinHttpReceiveResponse(request.value, nullptr)) {
            std::cerr << "Request " << i << " failed; stopped. Windows error: " << GetLastError() << "\n";
            return 1;
        }
        DWORD status = 0, size = sizeof(status);
        if (!WinHttpQueryHeaders(request.value, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                                 WINHTTP_HEADER_NAME_BY_INDEX, &status, &size, WINHTTP_NO_HEADER_INDEX)) {
            std::cerr << "Could not read response status: " << GetLastError() << "\n"; return 1;
        }
        std::cout << "Request " << i << ": HTTP " << status << "\n";
        if (status == 403 || status == 429) { std::cout << "Stopped after a block/rate-limit response.\n"; break; }
        if (status >= 500) { std::cout << "Server error; stopped.\n"; break; }
        if (i < count[0] - '0') std::this_thread::sleep_for(std::chrono::seconds(2));
    }
    std::cout << "\nHTTP status alone does not prove login succeeded or that protection is working.\n"
                 "Check server logs. Forms with CSRF, CAPTCHA, MFA or cookies need a dedicated test.\n";
    return 0;
}
#else
#include <iostream>
int main() { std::cerr << "This tool uses WinHTTP and must be run on Windows.\n"; return 1; }
#endif
