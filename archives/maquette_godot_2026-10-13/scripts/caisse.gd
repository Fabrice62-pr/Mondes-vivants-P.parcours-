class_name Caisse
extends Node
## Comptabilité stricte de la Caisse Coopérative de Brévanne.
## Actif : réserves (liquides) + prêts (illiquides). Passif : dépôts + fonds propres.

signal defaulted

var initial_deposits := 0.0
var deposits := 0.0
var reserves := 0.0
var loans := 0.0
var equity := 0.0
var loan_loss := 0.0        # part des prêts réellement perdue si l'emprunteur fait défaut
var loss_realized := false
var withdrawn_total := 0.0
var failed := false


func setup(total_deposits: float, reserve_ratio: float, p_loan_loss: float) -> void:
	initial_deposits = total_deposits
	deposits = total_deposits
	equity = total_deposits * 0.08
	reserves = total_deposits * reserve_ratio
	loans = total_deposits + equity - reserves
	loan_loss = p_loan_loss
	loss_realized = false
	withdrawn_total = 0.0
	failed = false


## La difficulté de l'emprunteur devient réelle (panique "justifiée").
func realize_loss() -> void:
	loss_realized = true


func loans_value() -> float:
	return loans * (1.0 - loan_loss) if loss_realized else loans


## Actif - dépôts. Négatif : la Caisse est insolvable.
func solvency_margin() -> float:
	return reserves + loans_value() - deposits


func is_solvent() -> bool:
	return solvency_margin() >= 0.0


## Paie au plus ce qu'il y a en caisse. Si la demande dépasse les réserves : défaut.
func withdraw(amount: float) -> float:
	if failed or amount <= 0.0:
		return 0.0
	if amount <= reserves:
		reserves -= amount
		deposits -= amount
		withdrawn_total += amount
		return amount
	var paid := reserves
	reserves = 0.0
	deposits -= paid
	withdrawn_total += paid
	failed = true
	defaulted.emit()
	return paid


func reserve_ratio_now() -> float:
	return reserves / deposits if deposits > 0.0 else 0.0
