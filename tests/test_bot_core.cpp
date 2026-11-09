#include "../src/bot_core.h"
#include <cassert>
int main() {
    assert(url_encode("a b&=+@") == "a%20b%26%3D%2B%40");
    assert(url_encode("abc-._~") == "abc-._~");
    assert(json_string("a\"\\\n") == "\"a\\\"\\\\\\u000a\"");
    assert(make_body(false, "user", "a&b", "pass", "1+2") == "user=a%26b&pass=1%2B2");
    assert(make_body(true, "user", "a\"b", "pass", "x") == "{\"user\":\"a\\\"b\",\"pass\":\"x\"}");
}
