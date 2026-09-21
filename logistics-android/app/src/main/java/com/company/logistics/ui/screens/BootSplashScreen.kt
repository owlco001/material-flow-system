package com.company.logistics.ui.screens

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.company.logistics.R
import com.company.logistics.ui.theme.LogisticsColors
import com.company.logistics.ui.theme.LogisticsTheme

/** Brand first-frame screen shown while the app restores its session. */
@Composable
fun BootSplashScreen(
    onFinished: () -> Unit,
    reduceMotion: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val currentOnFinished by rememberUpdatedState(onFinished)
    var finishDispatched by rememberSaveable { mutableStateOf(false) }
    val timeline = remember(reduceMotion) {
        Animatable(if (reduceMotion) 1f else 0f)
    }

    LaunchedEffect(reduceMotion) {
        if (finishDispatched) return@LaunchedEffect

        if (reduceMotion) {
            timeline.snapTo(1f)
        } else {
            timeline.animateTo(
                targetValue = 1f,
                animationSpec = tween(
                    durationMillis = SPLASH_DURATION_MILLIS,
                    easing = LinearEasing,
                ),
            )
        }

        // Set the guard before invoking the parent state change. The splash is
        // removed immediately after the callback and must never dispatch twice.
        finishDispatched = true
        currentOnFinished()
    }

    val progress = timeline.value
    val backgroundAlpha = easedPhase(progress, 0f, 0.18f)
    val iconAlpha = easedPhase(progress, 0.06f, 0.28f)
    val iconScale = 0.84f + 0.16f * easedPhase(progress, 0.06f, 0.32f)
    val gearRotation = 360f * easedPhase(progress, 0.04f, 0.78f)
    val arcProgress = easedPhase(progress, 0.12f, 0.78f)
    val arcAlpha =
        easedPhase(progress, 0.12f, 0.22f) * (1f - easedPhase(progress, 0.68f, 0.84f))
    val titleAlpha = easedPhase(progress, 0.56f, 0.75f)
    val subtitleAlpha = easedPhase(progress, 0.70f, 0.90f)

    LogisticsTheme {
        Box(
            modifier = modifier
                .fillMaxSize()
                .alpha(backgroundAlpha),
        ) {
            Canvas(Modifier.fillMaxSize()) {
                drawRect(
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            Color(0xFF0B2E6F),
                            Color(0xFF08214F),
                        ),
                    ),
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(
                            LogisticsColors.Primary.copy(alpha = 0.34f),
                            Color.Transparent,
                        ),
                    ),
                    radius = size.minDimension * 0.68f,
                    center = Offset(size.width * 0.5f, size.height * 0.34f),
                )
            }

            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .align(Alignment.Center)
                    .padding(horizontal = 24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Box(
                    modifier = Modifier.size(206.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Canvas(
                        modifier = Modifier
                            .size(190.dp)
                            .rotate(-90f + 430f * arcProgress)
                            .alpha(arcAlpha),
                    ) {
                        val radius = size.minDimension * 0.43f
                        drawCircle(
                            color = Color.White.copy(alpha = 0.10f),
                            radius = radius,
                            style = Stroke(width = 1.5.dp.toPx()),
                        )
                        drawArc(
                            brush = Brush.sweepGradient(
                                colors = listOf(
                                    Color.Transparent,
                                    LogisticsColors.Info.copy(alpha = 0.25f),
                                    Color.White.copy(alpha = 0.96f),
                                    LogisticsColors.Info.copy(alpha = 0.25f),
                                    Color.Transparent,
                                ),
                            ),
                            startAngle = -58f,
                            sweepAngle = 118f,
                            useCenter = false,
                            style = Stroke(
                                width = 4.dp.toPx(),
                                cap = StrokeCap.Round,
                            ),
                        )
                    }

                    Box(
                        modifier = Modifier
                            .size(170.dp)
                            .clip(CircleShape)
                            .background(
                                Brush.radialGradient(
                                    colors = listOf(
                                        Color.White.copy(alpha = 0.14f),
                                        Color.Transparent,
                                    ),
                                ),
                            ),
                        contentAlignment = Alignment.Center,
                    ) {
                        Image(
                            painter = painterResource(R.mipmap.ic_launcher_foreground),
                            contentDescription = null,
                            contentScale = ContentScale.Fit,
                            modifier = Modifier
                                .size(154.dp)
                                .scale(iconScale)
                                .rotate(gearRotation)
                                .alpha(iconAlpha),
                        )
                    }
                }

                Spacer(Modifier.height(24.dp))
                Text(
                    text = stringResource(R.string.brand_tagline),
                    color = Color.White,
                    fontSize = 32.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 2.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .offset(y = (8f * (1f - titleAlpha)).dp)
                        .alpha(titleAlpha),
                )

                Spacer(Modifier.height(8.dp))
                Text(
                    text = stringResource(R.string.app_name),
                    color = Color.White.copy(alpha = 0.88f),
                    fontSize = 20.sp,
                    fontWeight = FontWeight.SemiBold,
                    letterSpacing = 1.2.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .offset(y = (8f * (1f - titleAlpha)).dp)
                        .alpha(titleAlpha),
                )

                Spacer(Modifier.height(8.dp))
                Text(
                    text = stringResource(R.string.brand_subtitle),
                    color = Color.White.copy(alpha = 0.72f),
                    fontSize = 14.sp,
                    letterSpacing = 0.5.sp,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .offset(y = (8f * (1f - subtitleAlpha)).dp)
                        .alpha(subtitleAlpha),
                )
            }
        }
    }
}

private fun easedPhase(progress: Float, start: Float, end: Float): Float =
    FastOutSlowInEasing.transform(((progress - start) / (end - start)).coerceIn(0f, 1f))

private const val SPLASH_DURATION_MILLIS = 2_200
