/* @swarmevolve:guards-injected */
// This file already carries the marker. The injector must treat a second
// pass as a no-op (idempotence).
int main() {
    int s = 0;
#line 5 "injected"
    int _g_swarmevolve_0 = 0; /* @swarmevolve:guard */
    for (int i = 0; i < 10; ++i) {
        if (++_g_swarmevolve_0 > 1000)
            break; /* @swarmevolve:guard */
        s += i;
    }
    return (s == 45) ? 0 : 1;
}
