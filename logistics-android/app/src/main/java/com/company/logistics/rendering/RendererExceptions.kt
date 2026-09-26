package com.company.logistics.rendering

/**
 * 渲染引擎不可用时的统一异常。
 *
 * 单独放在 main sourceSet：它不依赖任何 filament 类，
 * release 包（无 filament）中的错误归因逻辑与单测仍可引用它。
 */
class FilamentUnavailableException(cause: Throwable?) :
    IllegalStateException("FILAMENT_UNAVAILABLE", cause)
