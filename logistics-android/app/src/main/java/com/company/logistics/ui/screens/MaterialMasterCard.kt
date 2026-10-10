package com.company.logistics.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.data.remote.MaterialFlowApi
import com.company.logistics.model.MaterialMaster
import com.company.logistics.ui.components.AppCard
import com.company.logistics.ui.components.VSpace
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme
import com.company.logistics.ui.theme.Spacing

/**
 * 物料详情页的「主档」卡片：形态、存储地点、图号、设备编号等 U9 主档属性。
 * 未导入主档的料号整张卡片不显示；加载失败只给一行提示和重试，不打断库存操作。
 */
@Composable
fun MaterialMasterCard(api: MaterialFlowApi, code: String) {
    var master by remember(code) { mutableStateOf<MaterialMaster?>(null) }
    var loaded by remember(code) { mutableStateOf(false) }
    var error by remember(code) { mutableStateOf<String?>(null) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(code, reload) {
        error = null
        runCatching { api.materialMaster(code) }
            .onSuccess { master = it; loaded = true }
            .onFailure { e ->
                if (e is kotlinx.coroutines.CancellationException) throw e
                error = e.message ?: "主档加载失败"
            }
    }

    val m = master
    if (error == null && (!loaded || m == null || m.rows.isEmpty())) return

    androidx.compose.foundation.layout.Column {
    AppCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(width = 4.dp, height = 16.dp)
                    .clip(RoundedCornerShape(2.dp))
                    .background(LogisticsColors.Primary),
            )
            Spacer(Modifier.width(8.dp))
            Text(
                "物料主档",
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                color = LogisticsTheme.colors.textPrimary,
                modifier = Modifier.weight(1f),
            )
            if (m != null && !m.effective) {
                Surface(shape = RoundedCornerShape(8.dp), color = LogisticsTheme.colors.danger.copy(alpha = 0.12f)) {
                    Text("已失效", fontSize = 11.sp, fontWeight = FontWeight.Bold, color = LogisticsTheme.colors.danger,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
                }
            }
            if (error != null) {
                TextButton(onClick = { reload++ }) { Text("重试", fontSize = 12.sp) }
            }
        }
        VSpace(Spacing.sm)
        if (error != null) {
            Text("主档加载失败：$error", fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary)
            return@AppCard
        }
        m?.rows?.forEach { (label, value) ->
            Row(Modifier.fillMaxWidth().padding(vertical = 3.dp)) {
                Text(label, fontSize = 13.sp, color = LogisticsTheme.colors.textSecondary, modifier = Modifier.width(76.dp))
                Text(value, fontSize = 13.sp, color = LogisticsTheme.colors.textPrimary, fontWeight = FontWeight.Medium,
                    textAlign = TextAlign.Start, modifier = Modifier.weight(1f))
            }
        }
    }
    VSpace(Spacing.md)
    }
}
