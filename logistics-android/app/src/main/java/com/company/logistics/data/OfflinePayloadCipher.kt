package com.company.logistics.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.nio.charset.StandardCharsets
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

interface OfflinePayloadCipher {
    fun encrypt(plainText: String): String
    fun decrypt(storedValue: String): String
}

/** Keystore-backed boundary; legacy plaintext rows remain readable for migration. */
class KeystoreOfflinePayloadCipher(private val alias: String = "offline-payload-key") : OfflinePayloadCipher {
    override fun encrypt(plainText: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        return PREFIX + Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(cipher.doFinal(plainText.toByteArray(StandardCharsets.UTF_8)), Base64.NO_WRAP)
    }
    override fun decrypt(storedValue: String): String {
        if (!storedValue.startsWith(PREFIX)) return storedValue
        val parts = storedValue.removePrefix(PREFIX).split(":", limit = 2)
        require(parts.size == 2) { "Invalid encrypted offline payload" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)))
        return String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8)
    }
    private fun key(): SecretKey {
        val store = java.security.KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!store.containsAlias(alias)) KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
            generateKey()
        }
        return (store.getEntry(alias, null) as java.security.KeyStore.SecretKeyEntry).secretKey
    }
    companion object { private const val TRANSFORMATION = "AES/GCM/NoPadding"; private const val PREFIX = "v1:" }
}
