package com.shushunyam.app

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.shushunyam.cadia.CadiaEngine
import com.shushunyam.cadia.CoreCommand
import com.shushunyam.voxshadow.viewmodel.VoxShadowViewModel
import com.shushunyam.voxshadow.viewmodel.VoxState

class MainActivity : AppCompatActivity() {

    private lateinit var viewModel: VoxShadowViewModel

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // инициализация ядра и рега модулей
        AppInitializer.init(this)

        setContentView(R.layout.activity_main)

        // создаем ViewModel
        viewModel = VoxShadowViewModel()

        val textTop = findViewById<TextView>(R.id.textTop)
        val textBottom = findViewById<TextView>(R.id.textBottom)
        val buttonTop = findViewById<Button>(R.id.buttonTop)
        val buttonBottom = findViewById<Button>(R.id.buttonBottom)

        // подписка на изменения состояния
        viewModel.onStateChanged = { state: VoxState ->
            runOnUiThread {
                textTop.text = state.topText
                textBottom.text = state.bottomText

                buttonTop.text = if (state.isListeningTop) "⏹ KR" else "🎙 KR"
                buttonBottom.text = if (state.isListeningBottom) "⏹ RU" else "🎙 RU"
            }
        }

        // обработчики кнопок
        buttonTop.setOnClickListener {
            ensureAudioPermission {
                viewModel.toggleTopListening()
            }
        }

        buttonBottom.setOnClickListener {
            ensureAudioPermission {
                viewModel.toggleBottomListening()
            }
        }

        // для соответствия архитектуре можем формально дернуть запуск модуля
        CadiaEngine.dispatchCommand(CoreCommand.OpenVoxShadow)
    }

    private fun ensureAudioPermission(onGranted: () -> Unit) {
        val granted = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.RECORD_AUDIO
        ) == PackageManager.PERMISSION_GRANTED

        if (granted) {
            onGranted()
        } else {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                100
            )
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 100 && grantResults.isNotEmpty() &&
            grantResults[0] == PackageManager.PERMISSION_GRANTED
        ) {
            // Ничего не делаем автоматически, пользователь сам снова жмёт кнопку
        }
    }
}
