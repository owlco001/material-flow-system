package com.company.logistics.ui.components

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.ui.theme.LogisticsTheme
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter

/**
 * 流转码展示 —— 审核通过后，相关角色在 APP 里展示此二维码，交接方扫码交接。
 * 二维码内容为流转单 document_no，与服务端 /api/v1/barcodes/transfer/{no} 一致，本地生成无需联网。
 */
@Composable
fun FlowQrCodeDialog(
    documentNo: String,
    onDismiss: () -> Unit,
) {
    val bitmap = remember(documentNo) { generateQrBitmap(documentNo, 640) }
    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("关闭") } },
        title = {
            Text(
                "流转码",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary,
            )
        },
        text = {
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                if (bitmap != null) {
                    Image(
                        bitmap = bitmap.asImageBitmap(),
                        contentDescription = "流转码二维码",
                        modifier = Modifier.size(240.dp),
                    )
                } else {
                    Text("二维码生成失败", fontSize = 13.sp, color = MaterialTheme.colorScheme.error)
                }
                Spacer(Modifier.height(12.dp))
                Text(
                    documentNo,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = LogisticsTheme.colors.textPrimary,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "交接方用 APP 扫码，勾选物料后确认交接",
                    fontSize = 12.sp,
                    color = LogisticsTheme.colors.textSecondary,
                    textAlign = TextAlign.Center,
                )
            }
        },
    )
}

private fun generateQrBitmap(content: String, sizePx: Int): Bitmap? {
    return try {
        val matrix = QRCodeWriter().encode(content, BarcodeFormat.QR_CODE, sizePx, sizePx)
        val bitmap = Bitmap.createBitmap(sizePx, sizePx, Bitmap.Config.RGB_565)
        for (x in 0 until sizePx) {
            for (y in 0 until sizePx) {
                bitmap.setPixel(x, y, if (matrix.get(x, y)) 0xFF000000.toInt() else 0xFFFFFFFF.toInt())
            }
        }
        bitmap
    } catch (_: Exception) {
        null
    }
}
