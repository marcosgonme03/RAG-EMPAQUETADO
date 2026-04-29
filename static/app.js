// Modal helpers
function abrirModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.style.display = 'flex';
    modal.querySelector('input, textarea')?.focus();
  }
}

function cerrarModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.style.display = 'none';
}

// Cerrar modal al hacer clic fuera
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.style.display = 'none';
  }
});

// Cerrar con Escape
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay').forEach(m => m.style.display = 'none');
  }
});
