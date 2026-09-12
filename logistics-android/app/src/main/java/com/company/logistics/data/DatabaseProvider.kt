package com.company.logistics.data

import android.content.Context
import androidx.room.Room

object DatabaseProvider {
    @Volatile private var instance: LogisticsDatabase? = null

    fun get(context: Context): LogisticsDatabase = instance ?: synchronized(this) {
        instance ?: Room.databaseBuilder(
            context.applicationContext,
            LogisticsDatabase::class.java,
            "logistics.db"
        ).build().also { instance = it }
    }
}
