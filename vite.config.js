import { defineConfig } from 'vite'
import { resolve } from 'path'

export default defineConfig({
  // Build configuration
  build: {
    // Output directory for built assets
    outDir: 'vibe_coding/static/vibe_coding/js/dist',
    emptyOutDir: true,
    
    // Entry point for active pixel war implementation
    rollupOptions: {
      input: {
        'pixel-war-refactored': resolve(__dirname, 'vibe_coding/static/vibe_coding/js/pixel_war_refactored.js')
      },
      output: {
        // Generate separate chunks for heavy dependencies
        manualChunks: {
          'pixi-core': ['pixi.js'],
          'pixi-viewport': ['pixi-viewport'],
          'mobile-helpers': ['hammerjs']
        },
        // Ensure proper naming for Django static files
        entryFileNames: '[name].bundle.js',
        chunkFileNames: '[name]-[hash].chunk.js',
        assetFileNames: '[name]-[hash].[ext]'
      }
    },
    
    // Source maps for development debugging
    sourcemap: true,
    
    // Optimize for production
    minify: 'esbuild',
    target: 'es2020'
  },
  
  // Development server configuration
  server: {
    // Proxy to Django development server
    proxy: {
      // Forward all non-asset requests to Django
      '^(?!/node_modules|/@vite|/src).*': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    },
    cors: true,
    port: 3000
  },
  
  // Dependency optimization
  optimizeDeps: {
    include: [
      'pixi.js',
      'pixi-viewport',
      'hammerjs'
    ],
    // Pre-bundle heavy dependencies
    force: true
  },
  
  // Plugin configuration
  plugins: [
    // Custom plugin to handle Django integration
    {
      name: 'django-integration',
      generateBundle(options, bundle) {
        // Generate a manifest file for Django to know about built files
        const manifest = {};
        
        for (const [fileName, chunk] of Object.entries(bundle)) {
          if (chunk.type === 'chunk' && chunk.isEntry) {
            // Map original entry names to built file names
            const originalName = chunk.name || chunk.facadeModuleId?.split('/').pop()?.replace('.js', '');
            manifest[originalName] = fileName;
          }
        }
        
        // Write manifest for Django static file integration
        this.emitFile({
          type: 'asset',
          fileName: 'manifest.json',
          source: JSON.stringify(manifest, null, 2)
        });
      }
    }
  ],
  
  // Base public path for assets
  base: '/static/vibe_coding/js/dist/',
  
  // Define globals for better tree-shaking
  define: {
    __DEV__: process.env.NODE_ENV !== 'production'
  }
})