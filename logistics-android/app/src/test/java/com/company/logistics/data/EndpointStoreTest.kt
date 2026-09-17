package com.company.logistics.data

import android.content.SharedPreferences
import com.company.logistics.BuildConfig
import java.lang.reflect.Constructor
import java.lang.reflect.Proxy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EndpointStoreTest {

    @Test
    fun normalizesValidHttpsAndHttpWithTrailingSlash() {
        assertEquals(
            "https://api.example.com:8443/path",
            EndpointStore.normalize("  https://api.example.com:8443/path///  "),
        )
        assertEquals("http://192.168.1.10:8000", EndpointStore.normalize("192.168.1.10:8000/"))
        assertEquals("http://192.168.1.10:8000", EndpointStore.normalize("http://192.168.1.10:8000///"))
    }

    @Test
    fun rejectsCredentialsQueryFragmentEmptyHostAndUnsupportedScheme() {
        listOf(
            "https://user:password@example.com",
            "https://example.com?token=secret",
            "https://example.com/path#fragment",
            "https:///missing-host",
            "https://",
            "ftp://example.com",
            "mailto:example.com",
        ).forEach { raw ->
            assertNull("Expected rejection for $raw", EndpointStore.normalize(raw))
        }
    }

    @Test
    fun saveNormalizesUrlAndReportsInvalidInput() {
        val store = newStore()

        val saved = store.save(" https://api.example.com/// ")
        assertTrue(saved.isSuccess)
        assertEquals("https://api.example.com", saved.getOrNull())
        assertEquals("https://api.example.com", store.savedUrl)

        val invalid = store.save("https://user:password@example.com")
        assertFalse(invalid.isSuccess)
        assertEquals(EndpointStore.ERR_INVALID, invalid.exceptionOrNull()?.message)
    }

    @Test
    fun debugBuildAllowsHttpButReleaseBuildRejectsItWithoutChangingProductionCode() {
        val store = newStore()
        val result = store.save("http://192.168.1.10:8000/")

        if (BuildConfig.DEBUG) {
            assertTrue(result.isSuccess)
            assertEquals("http://192.168.1.10:8000", result.getOrNull())
        } else {
            assertFalse(result.isSuccess)
            assertEquals(EndpointStore.ERR_RELEASE_HTTP, result.exceptionOrNull()?.message)
            assertNull(store.savedUrl)
        }
    }

    @Test
    fun releaseHttpGuardAlsoDiscardsPreviouslySavedHttpValue() {
        val store = newStore()
        val preferences = preferencesBackingStore
        preferences["api_base_url"] = "http://192.168.1.10:8000"

        if (BuildConfig.DEBUG) {
            assertEquals("http://192.168.1.10:8000", store.savedUrl)
        } else {
            assertNull(store.savedUrl)
        }
    }

    private val preferencesBackingStore = linkedMapOf<String, String?>()

    private fun newStore(): EndpointStore {
        preferencesBackingStore.clear()
        val preferences = Proxy.newProxyInstance(
            SharedPreferences::class.java.classLoader,
            arrayOf(SharedPreferences::class.java),
        ) { _, method, _ ->
            when (method.name) {
                "getString" -> preferencesBackingStore[method.parameterTypes.let { _ -> "api_base_url" }]
                "edit" -> newEditor()
                "contains" -> preferencesBackingStore.containsKey("api_base_url")
                "getAll" -> preferencesBackingStore.toMap()
                else -> defaultValue(method.returnType)
            }
        } as SharedPreferences

        val constructor: Constructor<EndpointStore> = EndpointStore::class.java
            .getDeclaredConstructor(SharedPreferences::class.java)
            .also { it.isAccessible = true }
        return constructor.newInstance(preferences)
    }

    private fun newEditor(): SharedPreferences.Editor {
        lateinit var editor: SharedPreferences.Editor
        editor = Proxy.newProxyInstance(
            SharedPreferences.Editor::class.java.classLoader,
            arrayOf(SharedPreferences.Editor::class.java),
        ) { _, method, args ->
            when (method.name) {
                "putString" -> {
                    preferencesBackingStore[args!![0] as String] = args[1] as String?
                    editor
                }
                "remove" -> {
                    preferencesBackingStore.remove(args!![0] as String)
                    editor
                }
                "apply", "commit" -> if (method.returnType == Boolean::class.javaPrimitiveType) true else null
                else -> defaultValue(method.returnType)
            }
        } as SharedPreferences.Editor
        return editor
    }

    private fun defaultValue(type: Class<*>): Any? = when (type) {
        Boolean::class.javaPrimitiveType -> false
        Int::class.javaPrimitiveType -> 0
        Long::class.javaPrimitiveType -> 0L
        Float::class.javaPrimitiveType -> 0f
        else -> null
    }
}
