#include <cstdio>
#include <fstream>
#include <iterator>
#include <vector>
#include "acl/acl.h"
#include "runtime/kernel.h"
#include "runtime/rt.h"

#ifndef PROBE_INPUT_BYTES
#define PROBE_INPUT_BYTES 512
#endif
#ifndef PROBE_OUTPUT_BYTES
#define PROBE_OUTPUT_BYTES 256
#endif

int main(int argc, char **argv) {
    if (argc != 3) return 64;
    std::ifstream bf(argv[1], std::ios::binary), inf("input.bin", std::ios::binary);
    std::vector<char> code((std::istreambuf_iterator<char>(bf)), {});
    std::vector<char> input((std::istreambuf_iterator<char>(inf)), {});
    if (code.empty() || input.size() != PROBE_INPUT_BYTES) return 65;
    std::vector<char> output(PROBE_OUTPUT_BYTES, char(0xa5));
    void *src=nullptr, *dst=nullptr, *handle=nullptr, *function=nullptr;
    rtStream_t stream=nullptr;
    rtDevBinary_t binary {};
    void *args[2] {};
    int rc=0;
    bool initialized=false, device=false;
#define CHECK(call) do { auto status=(call); if (status != 0) { \
    std::fprintf(stderr, "%s failed: %d\n", #call, int(status)); rc=1; goto cleanup; } } while(0)
    CHECK(aclInit(nullptr)); initialized=true;
    CHECK(rtSetDevice(0)); device=true;
    CHECK(rtStreamCreate(&stream, 0));
    CHECK(rtMalloc(&src, input.size(), RT_MEMORY_HBM, 0));
    CHECK(rtMalloc(&dst, output.size(), RT_MEMORY_HBM, 0));
    CHECK(rtMemcpy(src, input.size(), input.data(), input.size(), RT_MEMCPY_HOST_TO_DEVICE));
    CHECK(rtMemcpy(dst, output.size(), output.data(), output.size(), RT_MEMCPY_HOST_TO_DEVICE));
    binary.magic=RT_DEV_BINARY_MAGIC_ELF_AIVEC;
    binary.length=code.size(); binary.data=code.data();
    CHECK(rtDevBinaryRegister(&binary, &handle));
    CHECK(rtFunctionRegister(handle, argv[2], reinterpret_cast<const char_t *>(argv[2]), argv[2], 0));
    CHECK(rtGetFunctionByName(reinterpret_cast<const char_t *>(argv[2]), &function));
    args[0]=src; args[1]=dst;
    CHECK(rtKernelLaunch(function, 1, args, sizeof(args), nullptr, stream));
    CHECK(rtStreamSynchronize(stream));
    CHECK(rtMemcpy(output.data(), output.size(), dst, output.size(), RT_MEMCPY_DEVICE_TO_HOST));
    { std::ofstream f("output.bin", std::ios::binary); f.write(output.data(), output.size()); if(!f) rc=2; }
cleanup:
    if(dst) rtFree(dst);
    if(src) rtFree(src);
    if(stream) rtStreamDestroy(stream);
    if(device) rtDeviceReset(0);
    if(initialized) aclFinalize();
    return rc;
}
