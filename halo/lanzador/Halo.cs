// Halo.exe: arranca el servidor local si no está corriendo y abre la ventana propia del Halo.
//
// Se compila con el compilador de C# que trae Windows (halo/lanzador/construir.ps1); no necesita
// nada instalado aparte de Python y Microsoft Edge, que ya están. La ventana es Edge en modo
// aplicación: sin barra de direcciones, con un perfil propio en build/halo/ventana.
//
//   Halo.exe                  abre la pestaña activa
//   Halo.exe "?proyecto=..."  abre una pestaña concreta

using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

static class Halo
{
    const int Puerto = 8765;

    static bool Escucha(int puerto)
    {
        try
        {
            using (var cliente = new TcpClient())
            {
                var resultado = cliente.BeginConnect("127.0.0.1", puerto, null, null);
                if (!resultado.AsyncWaitHandle.WaitOne(400)) return false;
                cliente.EndConnect(resultado);
                return true;
            }
        }
        catch { return false; }
    }

    static string Raiz()
    {
        // El exe puede estar en la raíz del repositorio o en build/halo: se busca halo/servidor.py hacia arriba.
        var carpeta = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
        for (int i = 0; i < 6 && carpeta != null; i++, carpeta = carpeta.Parent)
            if (File.Exists(Path.Combine(carpeta.FullName, "halo", "servidor.py"))) return carpeta.FullName;
        return null;
    }

    static string Edge()
    {
        foreach (var base_ in new[] { Environment.GetEnvironmentVariable("ProgramFiles(x86)"), Environment.GetEnvironmentVariable("ProgramFiles") })
        {
            if (string.IsNullOrEmpty(base_)) continue;
            var ruta = Path.Combine(base_, "Microsoft", "Edge", "Application", "msedge.exe");
            if (File.Exists(ruta)) return ruta;
        }
        return null;
    }

    static int Error(string texto)
    {
        MessageBox.Show(texto, "Halo", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        return 1;
    }

    [STAThread]
    static int Main(string[] args)
    {
        var raiz = Raiz();
        if (raiz == null) return Error("No encuentro halo/servidor.py junto a Halo.exe.");
        if (!Escucha(Puerto))
        {
            try
            {
                var servidor = new ProcessStartInfo("python", "-m halo.servidor --puerto " + Puerto)
                {
                    WorkingDirectory = raiz, UseShellExecute = false, CreateNoWindow = true, WindowStyle = ProcessWindowStyle.Hidden,
                };
                Process.Start(servidor);
            }
            catch (Exception exc) { return Error("No pude arrancar el servidor del Halo con «python»:\n" + exc.Message); }
            var limite = DateTime.Now.AddSeconds(25);
            while (!Escucha(Puerto))
            {
                if (DateTime.Now > limite) return Error("El servidor del Halo no respondió en el puerto " + Puerto + ".");
                Thread.Sleep(200);
            }
        }
        var edge = Edge();
        if (edge == null) return Error("No encuentro Microsoft Edge, que es el motor de la ventana del Halo.");
        var perfil = Path.Combine(raiz, "build", "halo", "ventana");
        Directory.CreateDirectory(perfil);
        var url = "http://127.0.0.1:" + Puerto + "/" + (args.Length > 0 ? args[0] : "");
        Process.Start(new ProcessStartInfo(edge, "--app=" + url + " --user-data-dir=\"" + perfil + "\" --window-size=1440,900 --no-first-run --no-default-browser-check")
        {
            UseShellExecute = false, WorkingDirectory = raiz,
        });
        return 0;
    }
}
