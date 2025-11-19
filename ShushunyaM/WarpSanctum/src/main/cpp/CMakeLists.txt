cmake_minimum_required(VERSION 3.10)
project(warp_sanctum_native)

# our native library
add_library(
    warp_sanctum
    SHARED
    jni_bridge.cpp
    jni_bridge_llm.cpp
    native_stt.cpp
    native_llm.cpp
    native_tts.cpp
)

# GGML CORE
add_library(ggml STATIC
    ggml/ggml.c
    ggml/ggml-alloc.c
    ggml/ggml-cpu.c
    ggml/ggml-quants.c
)

# WHISPER
add_library(whisper STATIC
    whisper/whisper.cpp
)

# LLAMA
add_library(llama STATIC
    llama/llama.cpp
    llama/unicode.cpp
    llama/sampling.cpp
    llama/grammar.cpp
    llama/vocab.cpp
)

target_include_directories(warp_sanctum PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_include_directories(llama PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_include_directories(whisper PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

find_library(log-lib log)

target_link_libraries(
    warp_sanctum
    whisper
    llama
    ggml
    ${log-lib}
)
