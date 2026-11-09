// A small Windows browser helper: the user opens a page and explicitly chooses
// the focused field. No page scraping, automatic submit, or repeated attempts.
#ifdef _WIN32
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <winhttp.h>
#include <algorithm>
#include <string>
#include <vector>

namespace {
constexpr int ID_URL = 100, ID_TEXT = 101, ID_OPEN = 102, ID_ARM = 103, ID_CLEAR = 104;
constexpr int HOTKEY = 1, TIMER_EXPIRE = 1, TIMER_SEND = 2;
HWND url_edit = nullptr, text_edit = nullptr, status = nullptr, arm_button = nullptr;
HWND target_window = nullptr;
HFONT font = nullptr;
bool armed = false;
std::wstring pending_text;
ULONGLONG pending_until = 0;

void set_status(const wchar_t* message) { SetWindowTextW(status, message); }
std::wstring edit_text(HWND edit) {
    int length = GetWindowTextLengthW(edit);
    std::wstring value(static_cast<size_t>(length) + 1, L'\0');
    GetWindowTextW(edit, &value[0], length + 1);
    value.resize(static_cast<size_t>(length));
    return value;
}
void disarm(HWND hwnd) {
    if (armed) UnregisterHotKey(hwnd, HOTKEY);
    KillTimer(hwnd, TIMER_EXPIRE);
    KillTimer(hwnd, TIMER_SEND);
    armed = false;
    target_window = nullptr;
    pending_text.clear();
    SetWindowTextW(arm_button, L"Подготовить ввод");
}
bool valid_url(const std::wstring& url) {
    if (url.empty() || url.size() > 8192 || url.find_first_of(L"\r\n\t ") != std::wstring::npos) return false;
    URL_COMPONENTS parts{};
    parts.dwStructSize = sizeof(parts);
    parts.dwSchemeLength = parts.dwHostNameLength = parts.dwUserNameLength = parts.dwPasswordLength = static_cast<DWORD>(-1);
    return WinHttpCrackUrl(url.c_str(), static_cast<DWORD>(url.size()), 0, &parts) &&
           parts.nScheme == INTERNET_SCHEME_HTTPS && parts.dwHostNameLength > 0 &&
           parts.dwUserNameLength == 0 && parts.dwPasswordLength == 0;
}
void open_url(HWND hwnd) {
    const auto url = edit_text(url_edit);
    if (!valid_url(url)) {
        MessageBoxW(hwnd, L"Введите полный адрес HTTPS, например https://example.com/.\nАдрес не должен содержать логин или пароль.", L"Неверный адрес", MB_OK | MB_ICONWARNING);
        return;
    }
    auto result = reinterpret_cast<INT_PTR>(ShellExecuteW(hwnd, L"open", url.c_str(), nullptr, nullptr, SW_SHOWNORMAL));
    if (result <= 32) set_status(L"Не удалось открыть браузер. Проверьте браузер по умолчанию.");
    else set_status(L"Страница открывается в браузере по умолчанию.");
}
void arm(HWND hwnd) {
    if (armed) { disarm(hwnd); set_status(L"Ввод отменён."); return; }
    if (edit_text(text_edit).empty()) { set_status(L"Сначала введите текст для вставки."); return; }
    if (!RegisterHotKey(hwnd, HOTKEY, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 'V')) {
        set_status(L"Не удалось назначить Ctrl+Alt+V: сочетание занято другой программой."); return;
    }
    armed = true;
    SetWindowTextW(arm_button, L"Отменить ввод");
    if (!SetTimer(hwnd, TIMER_EXPIRE, 60000, nullptr)) {
        disarm(hwnd); set_status(L"Не удалось запустить таймер. Попробуйте снова."); return;
    }
    set_status(L"За 60 секунд выберите поле в браузере и нажмите Ctrl+Alt+V.");
}
void send_text(HWND hwnd) {
    if (GetForegroundWindow() != target_window || !target_window) {
        disarm(hwnd); set_status(L"Активное окно изменилось. Ввод отменён."); return;
    }
    std::vector<INPUT> inputs;
    inputs.reserve(pending_text.size() * 2);
    for (wchar_t c : pending_text) {
        INPUT down{}; down.type = INPUT_KEYBOARD; down.ki.wScan = c; down.ki.dwFlags = KEYEVENTF_UNICODE;
        INPUT up = down; up.ki.dwFlags |= KEYEVENTF_KEYUP;
        inputs.push_back(down); inputs.push_back(up);
    }
    UINT sent = SendInput(static_cast<UINT>(inputs.size()), inputs.data(), sizeof(INPUT));
    bool ok = sent == inputs.size();
    disarm(hwnd);
    set_status(ok ? L"Ввод отправлен в активное поле. Форма не отправлялась." :
                    L"Не весь текст удалось ввести. Проверьте поле и права окна браузера.");
}
HWND control(HWND parent, const wchar_t* cls, const wchar_t* text, DWORD style,
             int id, int x, int y, int w, int h) {
    HWND item = CreateWindowExW(cls == std::wstring(L"EDIT") ? WS_EX_CLIENTEDGE : 0,
                                cls, text, WS_CHILD | WS_VISIBLE | style,
                                x, y, w, h, parent, reinterpret_cast<HMENU>(static_cast<INT_PTR>(id)),
                                GetModuleHandleW(nullptr), nullptr);
    SendMessageW(item, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
    return item;
}
LRESULT CALLBACK window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
    case WM_CREATE: {
        font = static_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
        control(hwnd, L"STATIC", L"1. Адрес HTTPS:", 0, 0, 16, 18, 790, 20);
        url_edit = control(hwnd, L"EDIT", L"https://", WS_TABSTOP | ES_AUTOHSCROLL, ID_URL, 16, 42, 580, 26);
        SendMessageW(url_edit, EM_SETLIMITTEXT, 8192, 0);
        control(hwnd, L"BUTTON", L"Открыть сайт", WS_TABSTOP, ID_OPEN, 606, 40, 180, 30);
        control(hwnd, L"STATIC", L"2. Текст для ввода (не сохраняется):", 0, 0, 16, 90, 770, 20);
        text_edit = control(hwnd, L"EDIT", L"", WS_TABSTOP | ES_AUTOHSCROLL, ID_TEXT, 16, 114, 770, 26);
        SendMessageW(text_edit, EM_SETLIMITTEXT, 4096, 0);
        control(hwnd, L"STATIC", L"3. Нажмите «Подготовить ввод», выберите поле на странице и нажмите Ctrl+Alt+V.", 0, 0, 16, 162, 770, 38);
        arm_button = control(hwnd, L"BUTTON", L"Подготовить ввод", WS_TABSTOP, ID_ARM, 16, 204, 190, 34);
        control(hwnd, L"BUTTON", L"Очистить текст", WS_TABSTOP, ID_CLEAR, 216, 204, 160, 34);
        control(hwnd, L"STATIC", L"Ввод происходит один раз в активное поле. Кнопки сайта не нажимаются.\nТекст виден в окне. Не вводите чужие данные.", 0, 0, 16, 257, 770, 42);
        status = control(hwnd, L"STATIC", L"Готово.", 0, 0, 16, 318, 770, 42);
        return 0;
    }
    case WM_COMMAND:
        if (HIWORD(wparam) == BN_CLICKED) {
            switch (LOWORD(wparam)) {
            case ID_OPEN: open_url(hwnd); return 0;
            case ID_ARM: arm(hwnd); return 0;
            case ID_CLEAR: disarm(hwnd); SetWindowTextW(text_edit, L""); set_status(L"Текст очищен, ввод отменён."); return 0;
            }
        }
        break;
    case WM_HOTKEY:
        if (wparam == HOTKEY && armed) {
            target_window = GetForegroundWindow();
            if (!target_window || target_window == hwnd || IsChild(hwnd, target_window)) {
                set_status(L"Сначала выберите поле в другом окне, затем нажмите Ctrl+Alt+V."); return 0;
            }
            pending_text = edit_text(text_edit);
            pending_until = GetTickCount64() + 3000;
            if (!SetTimer(hwnd, TIMER_SEND, 30, nullptr)) {
                disarm(hwnd); set_status(L"Не удалось запустить ввод.");
            }
            return 0;
        }
        break;
    case WM_TIMER:
        if (wparam == TIMER_EXPIRE) { disarm(hwnd); set_status(L"Время ожидания истекло. Подготовьте ввод снова."); return 0; }
        if (wparam == TIMER_SEND) {
            if (GetTickCount64() >= pending_until) { disarm(hwnd); set_status(L"Отпустите клавиши и попробуйте снова."); }
            else if (!(GetAsyncKeyState(VK_CONTROL) & 0x8000) && !(GetAsyncKeyState(VK_MENU) & 0x8000) &&
                     !(GetAsyncKeyState(VK_SHIFT) & 0x8000) && !(GetAsyncKeyState('V') & 0x8000)) send_text(hwnd);
            return 0;
        }
        break;
    case WM_DESTROY: disarm(hwnd); PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(hwnd, message, wparam, lparam);
}
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    WNDCLASSW wc{};
    wc.lpfnWndProc = window_proc; wc.hInstance = instance;
    wc.lpszClassName = L"HttpsFillAssistantWindow";
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = reinterpret_cast<HBRUSH>(COLOR_WINDOW + 1);
    if (!RegisterClassW(&wc)) return 1;
    HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"Помощник ввода на сайте", WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                                CW_USEDEFAULT, CW_USEDEFAULT, 820, 410, nullptr, nullptr, instance, nullptr);
    if (!hwnd) return 1;
    ShowWindow(hwnd, show); UpdateWindow(hwnd);
    MSG msg;
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        if (!IsDialogMessageW(hwnd, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
    }
    return 0;
}
#else
int main() { return 1; }
#endif
