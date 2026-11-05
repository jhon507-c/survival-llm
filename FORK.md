# Este fork: servidor para Open WebUI

Fork de [anonnerdfrenzy/survival-llm](https://github.com/anonnerdfrenzy/survival-llm), partiendo de la versión 0.4.1
(commit `7d1fe29`). Todo lo que no se menciona aquí es igual al original; el [README](README.md) sigue valiendo.

**Para qué sirve:** un PC con tarjeta gráfica hace de servidor, y otros equipos de la red lo usan desde
[Open WebUI](https://openwebui.com) (gratis, de código abierto) con el modelo, la biblioteca offline, las instrucciones de
seguridad y la verificación de fuentes del proyecto. Apuntar Open WebUI directamente a Ollama da solo el modelo pelado:
sin biblioteca, sin esas instrucciones y sin la verificación de fuentes, que es lo que evita que el modelo cite
artículos que nunca abrió.

## Qué se modificó

| Archivo | Cambio |
|---|---|
| `app/openai_api.py` (nuevo) | La API estilo OpenAI: `GET /v1/models` y `POST /v1/chat/completions`, con y sin streaming, protegida con una clave Bearer. |
| `app/server.py` | Opciones `--api [HOST:]PUERTO` y `--api-my-files`; clave en `api-key.txt` o en `SURVIVAL_LLM_API_KEY`; prompt y biblioteca propios del modo API (sin memoria, sin `show_article`, responder en el idioma de la persona y buscar en inglés: `API_LANGUAGE`); `library_free_gb`. |
| `app/static/app.js` | Las comprobaciones de espacio de la ventana de la biblioteca usan el disco donde irá la biblioteca. |
| `core.py` | Parámetro `screen=False` (sin `show_article`); `Fuentes:` se verifica como `Sources:`; corrección de `OLLAMA_HOST=0.0.0.0:11434`. |
| `packaging/build.sh` | Copia `openai_api.py` a la app y crea `Survival LLM Server.bat` en Windows. |
| `tests/openai_api_test.py` (nuevo) | Pruebas de la API con un modelo y una biblioteca simulados (no necesita Ollama). |
| `README.md` | Sección "Use it from other computers". |

### La API (`app/openai_api.py`)

- Cada pregunta pasa por el mismo `chat_events()` que la ventana de la app, así que valen las mismas instrucciones,
  la búsqueda en la biblioteca y la verificación de la línea `Sources:`.
- Las búsquedas y los artículos leídos salen como `reasoning_content` (Open WebUI los muestra como razonamiento); la
  respuesta termina con la línea `Sources:` ya verificada.
- La respuesta empieza a mostrarse tras unos 200 caracteres, para que un "Déjame buscar…" escrito antes de una
  búsqueda no se muestre como si fuera la respuesta. Si la app tiene que corregir texto que ya salió (raro), añade
  debajo la respuesta corregida, porque el protocolo no permite borrar.
- Las peticiones propias de Open WebUI (título del chat, etiquetas, preguntas sugeridas; empiezan con `### Task:`)
  van directo al modelo, sin biblioteca ni razonamiento, y con un tope de 1.024 tokens.
- Si el cliente se desconecta, se deja de generar.
- Los modelos se listan sin el sufijo `:latest`, igual que en la app.
- `reasoning_effort` del cliente (`low`, `medium`, `high`) equivale a 1.000, 4.000 y 16.000 tokens de razonamiento.
  Sin él, el razonamiento está apagado.

### Seguridad

- Sin `--api`, no cambia nada: la app sigue escuchando solo en `127.0.0.1`.
- Con `--api`, escucha en todas las redes del equipo y **todas** las peticiones exigen `Authorization: Bearer CLAVE`.
- **Sin memoria personal:** la IA no lee ni guarda sus notas sobre la persona, porque cualquiera con la clave podría
  leerlas. **Sin tus archivos propios**, salvo con `--api-my-files`.
- El tráfico va por HTTP sin cifrar: usa una red de confianza o una VPN (por ejemplo Tailscale).
- Recomendado: abrir el puerto en el firewall solo para la IP del equipo donde corre Open WebUI.

## Puesta en marcha

1. **Arrancar el servidor.**
   - Windows, app empaquetada: doble clic en `Survival LLM Server.bat` (dentro de la carpeta de la app). La consola
     muestra la dirección y la clave; cerrarla cierra la app.
   - Desde el código: `python app/server.py --api 8770`. Sin número, usa el puerto 8766; si ese puerto está ocupado
     por otro programa, la API no arranca (lo avisa en la consola), así que elige otro.
   - `--api 192.168.1.20:8770` la limita a una sola red.
2. **Completar la configuración inicial** de la app (modelo y biblioteca) la primera vez, en su ventana.
3. **En Open WebUI:** Admin Panel > Settings > Connections > añadir una conexión **OpenAI API** con
   `http://IP-DEL-SERVIDOR:8770/v1` y la clave (`api-key.txt` en la carpeta de ajustes: `%APPDATA%\Survival LLM` en
   Windows). Desactiva la conexión a Ollama si la tenías, para que no se use el modelo sin biblioteca.
4. **Firewall de Windows**, en PowerShell como administrador (cambia la IP por la de tu Open WebUI):
   ```powershell
   New-NetFirewallRule -DisplayName "Survival LLM API" -Direction Inbound -Protocol TCP -LocalPort 8770 -RemoteAddress 192.168.1.50 -Action Allow
   ```

## Respuestas en español

Las preguntas hechas desde otros programas responden en el idioma de la persona sin configurar nada. El prompt de la API
(`API_LANGUAGE` en `app/server.py`) le pide al modelo que **responda en el idioma en que le escriben, pero que busque en
la biblioteca con palabras clave en inglés**, porque la biblioteca (Wikipedia, MedlinePlus, Ready.gov, CDC…) está en
inglés y la búsqueda es por palabras clave: una búsqueda en español no encuentra nada.

Además, **`Fuentes:` se verifica igual que `Sources:`** (`SOURCES_WORD` en `core.py`). Los modelos que responden en
español escriben "Fuentes:", y antes esa línea quedaba tal cual, con títulos traducidos o inventados que el modelo
nunca leyó, y debajo la línea verificada de la app. Ahora, si nombra algo que no leyó, se reemplaza por la línea real.

**Cómo se probó** (qwen3.5:4b, tres preguntas en español con una biblioteca de prueba en inglés):

| Prompt | Respuestas en español | Primera búsqueda en inglés |
|---|---|---|
| Sin la línea de idioma | 2 de 3 (la del generador salió en inglés) | 2 de 3 |
| Con `API_LANGUAGE` (el actual) | 8 de 9 | 8 de 9 |
| Una redacción más fuerte ("Always reply in the language…") | 6 de 6 | 3 de 6 (una vez buscó solo en español, no encontró nada y respondió de memoria) |

Con un modelo pequeño no es 100 % seguro: alguna respuesta puede salir en inglés. Si pasa a menudo, añade en Open WebUI
una instrucción de sistema (Settings > General > System Prompt, o por modelo en Workspace > Models), por ejemplo
`Responde siempre en español.`; las instrucciones del cliente se añaden después de las de la app. La regla del
monóxido de carbono se mantuvo en todas las pruebas.

Lo que **sigue en inglés**:
- La línea `Sources:` que escribe la app cuando reemplaza la del modelo ("Sources: none opened…", "Sources: none (from
  its own knowledge)").
- Los pasos de búsqueda y lectura que se muestran como razonamiento ("Searched the library for…", "Read…").
- La ventana propia de la app.

## GPU NVIDIA antigua (GTX 10xx) con Ollama 0.40.x en Windows

En el PC donde se desarrolló esto (GTX 1060 6 GB, Ollama 0.40.14), el backend CUDA de Ollama se caía al cargar cualquier
modelo con `CUDA error: shared object initialization failed`, con o sin flash attention. El backend Vulkan sí funciona
(unos 17 tokens/s con un modelo de 7B, 23 de 29 capas en la GPU). Variables de entorno de usuario:

```powershell
[Environment]::SetEnvironmentVariable('OLLAMA_VULKAN', '1', 'User')
[Environment]::SetEnvironmentVariable('CUDA_VISIBLE_DEVICES', '-1', 'User')   # oculta CUDA a todos los programas, no solo a Ollama
[Environment]::SetEnvironmentVariable('OLLAMA_HOST', '127.0.0.1:11434', 'User')
```

Reinicia Ollama después, porque solo lee las variables al arrancar. Con la app de Ollama, el interruptor "Expose Ollama
to the network" pisa `OLLAMA_HOST`: apágalo si no quieres Ollama abierto a la red. Esto es una solución para ese
equipo, no una garantía para otros; si tu GPU funciona con CUDA, no lo necesitas.

## Correcciones que van con este fork

- **`OLLAMA_HOST=0.0.0.0:11434`** (cómo se comparte Ollama en la red): en Windows no se puede conectar a `0.0.0.0`, y la
  app no encontraba ningún modelo. Ahora `0.0.0.0`, `[::]` y vacío se leen como `127.0.0.1`, y el esquema `http://`
  y las IPv6 también se entienden.
- **Biblioteca en otro disco:** el espacio libre que mostraba la ventana de la biblioteca era el de la carpeta
  personal (normalmente C:), aunque la biblioteca estuviera en otro disco. El botón "Download Everything" quedaba
  desactivado por falta de espacio que en realidad sobraba. Ahora se mide el disco de la biblioteca
  (`library_free_gb`). Los modelos de Ollama siguen guardándose en `~/.ollama/models` (`OLLAMA_MODELS` los cambia).

## Pruebas

```bash
python tests/run_all.py          # todas, incluida openai_api_test.py; no necesitan Ollama ni biblioteca
python tests/openai_api_test.py  # solo las de la API
```

## Mantener el fork al día

```bash
git remote add upstream https://github.com/anonnerdfrenzy/survival-llm.git
git fetch upstream && git merge upstream/main
```

La app consulta `update.json` del proyecto original, no el de este fork. Cuando instala una versión oficial,
reemplaza su carpeta y se pierde la API: hay que volver a copiar `core.py`, `app/server.py`, `app/openai_api.py` y
`app/static/app.js` (y crear de nuevo `Survival LLM Server.bat`), o publicar versiones propias con
`packaging/build.sh` (necesita un Mac).
